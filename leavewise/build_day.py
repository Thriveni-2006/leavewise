"""Step 2: turn ONE day of raw GPS pings into stop pass times for the chosen routes.

For each bus run: drop duplicates / off-road / impossible jumps, pick the direction by which
route line fits best, project onto the line (distance along route), keep it moving forward,
then interpolate the time the bus reached each stop. Long gaps are NOT interpolated.
"""
import argparse
import os
import sqlite3
import numpy as np
import pandas as pd
import datetime as dt
from .common import Polyline, ist, IST

MAX_OFF_ROUTE_M = 80      # pings further than this from the route line are dropped
MAX_STEP_MS = 30.0        # >108 km/h between pings = GPS jump
MAX_GAP_S = 240           # do not interpolate a stop pass across a hole longer than this
MAX_GAP_M = 1500
RUN_SPLIT_S = 1800        # a 30 min silence starts a new run


def load_geo(data_dir):
    rs = pd.read_csv(os.path.join(data_dir, "route_stops.csv"), dtype={"route_id": str, "stop_id": str})
    sh = pd.read_csv(os.path.join(data_dir, "shapes.csv"), dtype={"route_id": str})
    geo = {}
    for (r, d), g in sh.groupby(["route_id", "direction"]):
        g = g.sort_values("seq")
        s = rs[(rs.route_id == r) & (rs.direction == d)].sort_values("seq")
        geo.setdefault(r, {})[int(d)] = dict(poly=Polyline(g.lat.values, g.lng.values), stops=s)
    return geo


def clean_monotone(d, t):
    """Keep pings that move forward at a believable speed. Returns kept indices."""
    kept, last_d, last_t, rej = [], None, None, 0
    for k in range(len(d)):
        if last_d is None:
            ok = True
        else:
            dt_ = max(t[k] - last_t, 1.0)
            ok = (d[k] >= last_d - 60) and ((d[k] - last_d) / dt_ <= MAX_STEP_MS)
        if ok or rej >= 5:      # after 5 rejects in a row, trust the new position
            kept.append(k)
            last_d, last_t, rej = max(d[k], last_d if last_d is not None else d[k]), t[k], 0
        else:
            rej += 1
    return np.array(kept, dtype=int)


def pick_direction(lat, lng, t, cands):
    best = None
    for dirn, g in cands.items():
        dist, res = g["poly"].project(lat, lng)
        ok = res <= MAX_OFF_ROUTE_M
        if ok.sum() < 10 or np.ptp(dist[ok]) < 500 or np.ptp(t[ok]) == 0:
            continue
        c = np.corrcoef(dist[ok], t[ok])[0, 1]
        if not np.isfinite(c) or c < 0.5:
            continue
        score = ok.sum() * c
        if best is None or score > best[0]:
            best = (score, dirn, dist, ok)
    return best


def split_resets(dist, L):
    cuts = [0]
    peak = dist[0]
    for k in range(1, len(dist)):
        if dist[k] < peak - 0.5 * L:
            cuts.append(k)
            peak = dist[k]
        peak = max(peak, dist[k])
    cuts.append(len(dist))
    return [np.arange(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)]


def stop_passes_for_run(d, t, stops):
    ud, idx = np.unique(d, return_index=True)
    tu = t[idx]
    if len(ud) < 5:
        return None
    sd = stops.shape_dist.values
    inside = (sd >= ud[0]) & (sd <= ud[-1])
    p = np.clip(np.searchsorted(ud, sd), 1, len(ud) - 1)
    valid = inside & ((tu[p] - tu[p - 1]) <= MAX_GAP_S) & ((ud[p] - ud[p - 1]) <= MAX_GAP_M)
    tt = np.interp(sd, ud, tu)
    return np.where(valid, tt, np.nan)


def process_day(db_path, data_dir, routes):
    geo = load_geo(data_dir)
    routes = [str(r) for r in routes if str(r) in geo]
    ids = ",".join(str(int(float(r))) + ".0" for r in routes)
    con = sqlite3.connect(db_path)
    q = ("SELECT vehicle_id, lat, lng, route_id, trip_id, timestamp FROM vehicle_feed "
         f"WHERE route_id IN ({ids})")
    df = pd.read_sql_query(q, con)
    con.close()
    stats = dict(raw=len(df))
    df["lat"] = pd.to_numeric(df.lat, errors="coerce")
    df["lng"] = pd.to_numeric(df.lng, errors="coerce")
    df["ts"] = pd.to_numeric(df.timestamp, errors="coerce")
    df = df.dropna(subset=["lat", "lng", "ts"])
    df = df[(df.lat.between(27, 30)) & (df.lng.between(76, 78.5))]
    df = df.drop_duplicates(subset=["vehicle_id", "ts"])
    df["rid"] = df.route_id.astype(float).astype(int).astype(str)
    df["trip_id"] = df.trip_id.fillna("")
    # the file's day = the IST day most pings belong to (a few stray pings can come from the day before)
    date = ist(df.ts.median()).strftime("%Y-%m-%d") if len(df) else ""
    if date:
        d0 = dt.datetime.fromisoformat(date).replace(tzinfo=IST).timestamp()
        df = df[(df.ts >= d0) & (df.ts < d0 + 86400)]
    stats["clean"] = len(df)
    rows, runs_ok, runs_bad = [], 0, 0
    for (veh, rid, trip), g in df.sort_values("ts").groupby(["vehicle_id", "rid", "trip_id"], sort=False):
        if rid not in geo:
            continue
        g = g.sort_values("ts")
        ts = g.ts.values
        brk = np.where(np.diff(ts) > RUN_SPLIT_S)[0] + 1
        for part, sl in enumerate(np.split(np.arange(len(g)), brk)):
            if len(sl) < 10:
                continue
            lat, lng, t = g.lat.values[sl], g.lng.values[sl], ts[sl]
            best = pick_direction(lat, lng, t, geo[rid])
            if best is None:
                runs_bad += 1
                continue
            _, dirn, dist, ok = best
            lat, lng, t, dist = lat[ok], lng[ok], t[ok], dist[ok]
            L = geo[rid][dirn]["poly"].length
            for k, seg in enumerate(split_resets(dist, L)):
                if len(seg) < 10:
                    continue
                keep = clean_monotone(dist[seg], t[seg])
                if len(keep) < 10:
                    continue
                d = np.maximum.accumulate(dist[seg][keep])
                tt = t[seg][keep]
                st = geo[rid][dirn]["stops"]
                pt = stop_passes_for_run(d, tt, st)
                if pt is None or np.isfinite(pt).sum() < 3:
                    runs_bad += 1
                    continue
                runs_ok += 1
                run_id = f"{date}|{veh}|{trip}|{part}.{k}"
                for seq, sid, tp in zip(st.seq.values, st.stop_id.values, pt):
                    if np.isfinite(tp):
                        rows.append((rid, dirn, run_id, int(seq), sid, round(float(tp), 1)))
    out = pd.DataFrame(rows, columns=["route_id", "direction", "run_id", "seq", "stop_id", "t"])
    out["date"] = date
    stats.update(date=date, runs_ok=runs_ok, runs_bad=runs_bad, stop_passes=len(out))
    return out, stats


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="one bus_movements_YYYY_MM_DD.db file")
    ap.add_argument("--data", default="data")
    ap.add_argument("--routes", required=True, help="comma separated route ids (same as in gtfs_prep)")
    ap.add_argument("--out", default="passes")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    out, stats = process_day(a.db, a.data, a.routes.split(","))
    path = os.path.join(a.out, f"passes_{stats['date']}.csv")
    out.to_csv(path, index=False)
    print(stats)
    print("Wrote", path)


if __name__ == "__main__":
    main()

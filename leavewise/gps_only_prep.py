"""BACKUP for step 1 when static GTFS is NOT available.
Builds data/route_stops.csv and data/shapes.csv from one day of GPS only:
  - route line  = the longest clean run of that route (smoothed, resampled every 50 m)
  - 2nd direction = longest run that does not follow the first line forwards
  - stops       = places where many different runs slow down / stand (<2 km/h), >=150 m apart
LIMITS (say this in the technical note): these are 'stop-like points' (they can include busy signals),
stop names are generated, and there is no timetable, so the timetable fallback level is unavailable.
"""
import argparse
import os
import sqlite3
import numpy as np
import pandas as pd
from .common import Polyline

BIN_M = 40
MIN_SPACING_M = 150


def clean_run(lat, lng, min_d=30.0):
    """Keep a ping only when it is >=30 m from the last kept one (removes standing jitter)
    and <300 m away (removes GPS jumps; after 5 rejects in a row we trust the new position)."""
    keep, rej = [0], 0
    k0 = np.cos(np.radians(lat[0]))
    for i in range(1, len(lat)):
        j = keep[-1]
        d = np.hypot((lng[i] - lng[j]) * 111320 * k0, (lat[i] - lat[j]) * 110540)
        if d >= 300 and rej < 5:
            rej += 1
        elif d >= min_d:
            keep.append(i)
            rej = 0
    return np.array(keep)


def extent_m(lat, lng):
    k0 = np.cos(np.radians(lat[0]))
    return float(np.hypot(np.ptp(lng) * 111320 * k0, np.ptp(lat) * 110540))


def resample(lat, lng, step=50.0):
    p = Polyline(lat, lng)
    s = np.arange(0, p.length, step)
    if len(s) < 5:
        return None
    la, ln = p.point_at(s)
    w = 5
    ker = np.ones(w) / w
    la = np.convolve(np.pad(la, w // 2, mode="edge"), ker, mode="valid")
    ln = np.convolve(np.pad(ln, w // 2, mode="edge"), ker, mode="valid")
    return la, ln


def best_run(runs, exclude=()):
    best, bext = None, 0
    for k, r in runs.items():
        if k in exclude or np.ptp(r["t"]) > 4 * 3600:
            continue
        keep = clean_run(r["lat"], r["lng"])
        if len(keep) < 30:
            continue
        la, ln = r["lat"][keep], r["lng"][keep]
        ext = extent_m(la, ln)
        L = Polyline(la, ln).length
        if L <= 2.0 * ext + 2000 and ext > bext:   # not a looping / multi-trip tangle
            best, bext = k, ext
    return best


def follows(poly, r):
    """Does this run drive along `poly` forwards for a big part of it?"""
    dist, res = poly.project(r["lat"], r["lng"])
    ok = res < 80
    if ok.sum() < 20 or np.ptp(dist[ok]) < 0.4 * poly.length:
        return False
    return np.corrcoef(dist[ok], r["t"][ok])[0, 1] > 0.5


def find_stops(poly, runs_in_dir):
    """runs_in_dir: list of dicts with lat,lng,t,speed that follow this line."""
    nb = int(poly.length // BIN_M) + 1
    seen = np.zeros(nb)
    for r in runs_in_dir:
        dist, res = poly.project(r["lat"], r["lng"])
        slow = (r["speed"] < 2.0) & (res < 60)
        bins = np.unique((dist[slow] // BIN_M).astype(int))
        seen[bins[bins < nb]] += 1
    n = max(len(runs_in_dir), 1)
    thr = max(4, 0.20 * n)
    sm = np.convolve(seen, np.ones(3), mode="same")
    peaks = [i for i in range(nb) if sm[i] >= thr and sm[i] == sm[max(0, i - 3):i + 4].max()]
    stops = []
    for i in sorted(peaks, key=lambda i: -sm[i]):
        d = (i + 0.5) * BIN_M
        if all(abs(d - s) >= MIN_SPACING_M for s in stops):
            stops.append(d)
    return sorted(stops)


def prep(db, routes, out, max_runs=150):
    con = sqlite3.connect(db)
    ids = ",".join(str(int(float(r))) + ".0" for r in routes)
    df = pd.read_sql_query("SELECT vehicle_id, lat, lng, speed, route_id, trip_id, timestamp FROM vehicle_feed "
                           f"WHERE route_id IN ({ids})", con)
    for c in ("lat", "lng", "speed", "timestamp"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["lat", "lng", "timestamp"]).drop_duplicates(subset=["vehicle_id", "timestamp"])
    df = df[df.lat.between(27, 30) & df.lng.between(76, 78.5)]
    df["rid"] = df.route_id.astype(float).astype(int).astype(str)
    rs_rows, sh_rows = [], []
    for rid, g in df.groupby("rid"):
        runs = {}
        for (veh, trip), x in g.sort_values("timestamp").groupby(["vehicle_id", "trip_id"]):
            if len(x) < 30:
                continue
            runs[(veh, trip)] = dict(lat=x.lat.values, lng=x.lng.values, t=x.timestamp.values,
                                     speed=x.speed.fillna(0).values)
            if len(runs) >= 400:
                break
        if len(runs) < 3:
            print("route", rid, "too few runs, skipped")
            continue
        refs = {}
        kA = best_run(runs)
        if kA is None:
            continue
        keep = clean_run(runs[kA]["lat"], runs[kA]["lng"])
        rsA = resample(runs[kA]["lat"][keep], runs[kA]["lng"][keep])
        if rsA is None:
            continue
        polyA = Polyline(*rsA)
        refs[0] = (polyA, [])
        others = {}
        for k, r in runs.items():
            if follows(polyA, r):
                refs[0][1].append(r)
            else:
                others[k] = r
        kB = best_run(others)
        if kB is not None:
            keep = clean_run(others[kB]["lat"], others[kB]["lng"])
            rsB = resample(others[kB]["lat"][keep], others[kB]["lng"][keep])
            if rsB is not None:
                polyB = Polyline(*rsB)
                members = []
                for r in others.values():
                    if follows(polyB, r):
                        members.append(r)
                refs[1] = (polyB, members)
        for d, (poly, members) in refs.items():
            members = members[:max_runs]
            st = find_stops(poly, members)
            if len(st) < 3:
                print("route", rid, "dir", d, "found <3 stops, skipped")
                continue
            la, ln = poly.point_at(np.array(st))
            for k, (a, b, s) in enumerate(zip(la, ln, st)):
                rs_rows.append(dict(route_id=rid, short_name=rid, direction=d, seq=k, stop_id=f"G{rid}_{d}_{k}",
                                    stop_name=f"Route {rid} stop {k + 1} (dir {d})", lat=a, lng=b,
                                    shape_dist=round(float(s), 1), sched_sec=np.nan))
            for k, (a, b) in enumerate(zip(poly.point_at(poly.cum)[0], poly.point_at(poly.cum)[1])):
                sh_rows.append(dict(route_id=rid, direction=d, seq=k, lat=a, lng=b))
            print(f"route {rid} dir {d}: {len(members)} runs, {len(st)} stop-like points, {poly.length / 1000:.1f} km")
    os.makedirs(out, exist_ok=True)
    pd.DataFrame(rs_rows).to_csv(os.path.join(out, "route_stops.csv"), index=False)
    pd.DataFrame(sh_rows).to_csv(os.path.join(out, "shapes.csv"), index=False)
    print("Wrote", out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="ONE good weekday GPS file")
    ap.add_argument("--routes", required=True)
    ap.add_argument("--out", default="data")
    a = ap.parse_args()
    prep(a.db, a.routes.split(","), a.out)


if __name__ == "__main__":
    main()

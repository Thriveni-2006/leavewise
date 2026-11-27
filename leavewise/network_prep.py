"""Whole-network layer: stop sequences + scheduled headways for EVERY route in the static GTFS.

Writes data/network_stops.csv (same columns as route_stops.csv, minus shapes) and data/headways.csv.
Routes without GPS history use these with the city pulse (see pulse.py), so any two points on the bus
network can get an answer. The answer is marked as an estimate (lower confidence).
"""
import argparse
import os
import numpy as np
import pandas as pd
from .common import haversine_m


def hms(s):
    p = s.str.split(":", expand=True)
    return p[0].astype(float) * 3600 + p[1].astype(float) * 60 + p[2].astype(float)


def prep(gtfs, out):
    os.makedirs(out, exist_ok=True)
    rt = pd.read_csv(os.path.join(gtfs, "routes.txt"), dtype=str)
    names = dict(zip(rt.route_id, rt["route_short_name"].fillna(rt.route_id) if "route_short_name" in rt.columns else rt.route_id))
    tr = pd.read_csv(os.path.join(gtfs, "trips.txt"), dtype=str)
    if "direction_id" not in tr.columns:
        tr["direction_id"] = "0"
    tr["direction_id"] = tr.direction_id.fillna("0").astype(str).str.replace(".0", "", regex=False)
    stops = pd.read_csv(os.path.join(gtfs, "stops.txt"), dtype=str)
    stops["lat"] = stops.stop_lat.astype(float)
    stops["lng"] = stops.stop_lon.astype(float)
    sinfo = stops.set_index("stop_id")[["stop_name", "lat", "lng"]]

    st = pd.read_csv(os.path.join(gtfs, "stop_times.txt"), dtype=str,
                     usecols=["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"])
    st["stop_sequence"] = st.stop_sequence.astype(int)
    st = st.sort_values(["trip_id", "stop_sequence"])
    st["arr"] = hms(st.arrival_time.fillna(st.departure_time))
    st["dep"] = hms(st.departure_time.fillna(st.arrival_time))

    g = st.groupby("trip_id", sort=False)
    trips = pd.DataFrame(dict(first=g.stop_id.first(), last=g.stop_id.last(), n=g.size(), dep0=g.dep.first()))
    trips = trips.join(tr.set_index("trip_id")[["route_id", "direction_id", "service_id"]], how="inner")

    # ---- representative pattern per route+direction: the most common (first stop, last stop, #stops)
    trips["sig"] = trips["first"] + ">" + trips["last"] + ">" + trips.n.astype(str)
    rep = {}
    for (r, d), x in trips.groupby(["route_id", "direction_id"]):
        sig = x.sig.value_counts().index[0]
        rep[(r, d)] = x[x.sig == sig].index[0]

    st_idx = st.set_index("trip_id")
    rows = []
    for (r, d), trip in rep.items():
        t = st_idx.loc[trip]
        if isinstance(t, pd.Series) or len(t) < 3:
            continue
        t = t[t.stop_id.isin(sinfo.index)]
        if len(t) < 3:
            continue
        lat = sinfo.loc[t.stop_id, "lat"].values
        lng = sinfo.loc[t.stop_id, "lng"].values
        step = [0.0] + [haversine_m(lat[k - 1], lng[k - 1], lat[k], lng[k]) for k in range(1, len(t))]
        dist = np.cumsum(step)
        base = t.arr.iloc[0]
        for k, (sid, a) in enumerate(zip(t.stop_id.values, t.arr.values)):
            rows.append((r, names.get(r, r), int(d), k, sid, sinfo.loc[sid, "stop_name"], lat[k], lng[k],
                         round(dist[k], 1), float(a - base)))
    net = pd.DataFrame(rows, columns=["route_id", "short_name", "direction", "seq", "stop_id", "stop_name",
                                      "lat", "lng", "shape_dist", "sched_sec"])
    net.to_csv(os.path.join(out, "network_stops.csv"), index=False)

    # ---- scheduled trips per hour (for the wait at stops with no GPS history)
    wk, we = set(), set()
    cal = os.path.join(gtfs, "calendar.txt")
    if os.path.exists(cal):
        c = pd.read_csv(cal, dtype=str)
        wd = c[(c[["monday", "tuesday", "wednesday", "thursday", "friday"]] == "1").any(axis=1)].service_id
        we_ = c[(c[["saturday", "sunday"]] == "1").any(axis=1)].service_id
        wk, we = set(wd), set(we_)
    else:
        wk = we = set(trips.service_id.unique())
    trips["hour"] = ((trips.dep0 // 3600) % 24).astype(int)
    parts = []
    for name, svc in (("weekday", wk), ("weekend", we)):
        x = trips[trips.service_id.isin(svc)]
        h = x.groupby(["route_id", "direction_id", "hour"]).size().rename("trips").reset_index()
        h["daytype"] = name
        parts.append(h)
    hw = pd.concat(parts).rename(columns={"direction_id": "direction"})
    hw["direction"] = hw.direction.astype(int)
    hw.to_csv(os.path.join(out, "headways.csv"), index=False)
    print(f"Wrote {len(net)} stop rows for {net.groupby(['route_id','direction']).ngroups} route-directions "
          f"({net.route_id.nunique()} routes) and {len(hw)} headway rows.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gtfs", required=True)
    ap.add_argument("--out", default="data")
    a = ap.parse_args()
    prep(a.gtfs, a.out)


if __name__ == "__main__":
    main()

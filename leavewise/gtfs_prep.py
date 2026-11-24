"""Step 1: shrink the big static GTFS to just the routes we model.

Writes data/route_stops.csv and data/shapes.csv.
Per route+direction we use the most common shape and one representative trip for stop order.
"""
import argparse
import os
import numpy as np
import pandas as pd
from .common import Polyline


def hms_to_sec(s):
    try:
        h, m, x = str(s).split(":")
        return int(h) * 3600 + int(m) * 60 + int(x)
    except Exception:
        return np.nan


def prep(gtfs, routes, out):
    os.makedirs(out, exist_ok=True)
    routes = [str(r) for r in routes]
    rt = pd.read_csv(os.path.join(gtfs, "routes.txt"), dtype=str)
    rt = rt[rt.route_id.isin(routes)]
    if rt.empty:
        raise SystemExit("None of your route ids exist in routes.txt. Example ids there: "
                         + ", ".join(pd.read_csv(os.path.join(gtfs, "routes.txt"), dtype=str).route_id.head(8)))
    name_col = "route_short_name" if "route_short_name" in rt.columns else "route_id"
    names = dict(zip(rt.route_id, rt[name_col].fillna(rt.route_id)))
    tr = pd.read_csv(os.path.join(gtfs, "trips.txt"), dtype=str)
    tr = tr[tr.route_id.isin(routes)].copy()
    if "shape_id" not in tr.columns:
        raise SystemExit("trips.txt has no shape_id column, cannot draw route lines.")
    if "direction_id" not in tr.columns:
        tr["direction_id"] = "0"
    tr["direction_id"] = tr.direction_id.fillna("0").astype(str).str.replace(".0", "", regex=False)

    rep = {}
    for (r, d), g in tr.groupby(["route_id", "direction_id"]):
        shape = g.shape_id.value_counts().index[0]
        rep[(r, d)] = (shape, g[g.shape_id == shape].trip_id.iloc[0])
    rep_trips = {v[1] for v in rep.values()}
    shape_ids = {v[0] for v in rep.values()}

    st = []
    for ch in pd.read_csv(os.path.join(gtfs, "stop_times.txt"), dtype=str, chunksize=500000):
        st.append(ch[ch.trip_id.isin(rep_trips)])
    st = pd.concat(st)
    st["stop_sequence"] = st.stop_sequence.astype(int)
    st["sec"] = st.arrival_time.map(hms_to_sec)
    stops = pd.read_csv(os.path.join(gtfs, "stops.txt"), dtype=str)
    stops["lat"] = stops.stop_lat.astype(float)
    stops["lng"] = stops.stop_lon.astype(float)

    sh = []
    for ch in pd.read_csv(os.path.join(gtfs, "shapes.txt"), dtype=str, chunksize=500000):
        sh.append(ch[ch.shape_id.isin(shape_ids)])
    sh = pd.concat(sh)
    sh["seq"] = sh.shape_pt_sequence.astype(int)
    sh["lat"] = sh.shape_pt_lat.astype(float)
    sh["lng"] = sh.shape_pt_lon.astype(float)

    rs_rows, sh_rows = [], []
    for (r, d), (shape, trip) in rep.items():
        s = sh[sh.shape_id == shape].sort_values("seq")
        poly = Polyline(s.lat.values, s.lng.values)
        t = st[st.trip_id == trip].sort_values("stop_sequence").merge(
            stops[["stop_id", "stop_name", "lat", "lng"]], on="stop_id", how="left")
        dist, res = poly.project(t.lat.values, t.lng.values)
        t = t.assign(shape_dist=dist, off=res)
        # keep stops in increasing distance (drop stops far off the line or going backwards)
        t = t[t.off < 150]
        t = t[t.shape_dist >= np.maximum.accumulate(t.shape_dist.values) - 1]
        t = t.reset_index(drop=True)
        t["seq"] = np.arange(len(t))
        for _, x in t.iterrows():
            rs_rows.append(dict(route_id=r, short_name=names.get(r, r), direction=int(d), seq=int(x.seq),
                                stop_id=x.stop_id, stop_name=x.stop_name, lat=x.lat, lng=x.lng,
                                shape_dist=round(x.shape_dist, 1),
                                sched_sec=(x.sec - t.sec.iloc[0]) if pd.notna(x.sec) else np.nan))
        for _, x in s.iterrows():
            sh_rows.append(dict(route_id=r, direction=int(d), seq=int(x.seq), lat=x.lat, lng=x.lng))
    pd.DataFrame(rs_rows).to_csv(os.path.join(out, "route_stops.csv"), index=False)
    pd.DataFrame(sh_rows).to_csv(os.path.join(out, "shapes.csv"), index=False)
    print(f"Wrote {len(rs_rows)} route-stop rows and {len(sh_rows)} shape points for {len(rep)} route-directions.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gtfs", required=True, help="folder with stops.txt, routes.txt, trips.txt, stop_times.txt, shapes.txt")
    ap.add_argument("--routes", required=True, help="comma separated GTFS route_id values, e.g. 534,764")
    ap.add_argument("--out", default="data")
    a = ap.parse_args()
    prep(a.gtfs, a.routes.split(","), a.out)


if __name__ == "__main__":
    main()

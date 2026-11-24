"""Makes FAKE data in the same format as the real hackathon data, so the code can be tested.
NOT real Delhi data. Never use results from this in your submission."""
import argparse
import datetime as dt
import os
import sqlite3
import numpy as np
import pandas as pd
from .common import Polyline, daytype_of_date, IST

RNG = np.random.default_rng(42)
FACTOR_WD = {**{h: 1.25 for h in range(0, 6)}, 6: 1.0, 7: 0.75, 8: 0.6, 9: 0.6, 10: 0.7,
             **{h: 0.85 for h in range(11, 17)}, 17: 0.65, 18: 0.6, 19: 0.62, 20: 0.75, 21: 1.1, 22: 1.15, 23: 1.2}
FACTOR_WE = {h: (1.1 if h < 9 or h > 21 else 0.95) for h in range(24)}


def headway(h, dtype):
    if dtype == "weekday" and (7 <= h <= 10 or 17 <= h <= 20):
        return 8 * 60
    return 30 * 60 if h >= 22 or h < 6 else 15 * 60


def make_route(k, n_stops=20, spacing=900):
    lat0, lng0 = 28.55 + 0.03 * k, 77.15 + 0.02 * k
    n = int(n_stops * spacing / 100)
    s = np.arange(n) * 100.0
    ang = 0.6 + 0.5 * k
    x = s * np.cos(ang) + 600 * np.sin(s / 4000.0)
    y = s * np.sin(ang) + 400 * np.cos(s / 3500.0)
    lat = lat0 + y / 110540.0
    lng = lng0 + x / (111320.0 * np.cos(np.radians(lat0)))
    return lat, lng, spacing


def build_static(out):
    rs, sh, routes, trips, stops_rows, st_rows = [], [], [], [], [], []
    for k in range(3):
        rid = str(500 + k)
        lat, lng, spacing = make_route(k)
        for d in (0, 1):
            la, ln = (lat, lng) if d == 0 else (lat[::-1] + 0.00012, lng[::-1] + 0.00012)
            poly = Polyline(la, ln)
            stop_d = np.arange(0, poly.length - 50, spacing)
            slat, slng = poly.point_at(stop_d)
            for q, (a, b, sd) in enumerate(zip(slat, slng, stop_d)):
                sid = f"S{rid}_{d}_{q}"
                rs.append(dict(route_id=rid, short_name=f"{rid}{'UP' if d == 0 else 'DN'}", direction=d, seq=q, stop_id=sid,
                               stop_name=f"Stop {rid} {chr(65 + q % 26)}{q // 26 or ''} ({'up' if d == 0 else 'dn'})",
                               lat=a, lng=b, shape_dist=round(sd, 1), sched_sec=q * spacing / (18 / 3.6)))
            for q, (a, b) in enumerate(zip(la, ln)):
                sh.append(dict(route_id=rid, direction=d, seq=q, lat=a, lng=b))
    os.makedirs(out, exist_ok=True)
    pd.DataFrame(rs).to_csv(os.path.join(out, "route_stops.csv"), index=False)
    pd.DataFrame(sh).to_csv(os.path.join(out, "shapes.csv"), index=False)
    return pd.DataFrame(rs), pd.DataFrame(sh)


def simulate_day(date, rs, sh, db_path):
    dtype = daytype_of_date(date)
    fac = FACTOR_WD if dtype == "weekday" else FACTOR_WE
    day0 = dt.datetime.fromisoformat(date).replace(tzinfo=IST).timestamp()
    rows = []
    for (rid, d), g in sh.groupby(["route_id", "direction"]):
        g = g.sort_values("seq")
        poly = Polyline(g.lat.values, g.lng.values)
        stops = rs[(rs.route_id == rid) & (rs.direction == d)]
        sd = stops.shape_dist.values
        t0 = day0 + 5 * 3600 + RNG.uniform(0, 600)
        veh_n = 0
        while t0 < day0 + 23.5 * 3600:
            h = int((t0 - day0) // 3600)
            hw = headway(h, dtype)
            veh_n += 1
            trip_f = np.exp(RNG.normal(0, 0.12)) * (0.65 if RNG.random() < 0.04 else 1.0)
            f = fac[h] * trip_f
            # distance->time with dwell at stops
            ds = np.arange(0, poly.length, 100.0)
            speed = 25 / 3.6 * f * np.exp(RNG.normal(0, 0.18, len(ds)))
            tt = np.concatenate([[0], np.cumsum(100.0 / np.maximum(speed[:-1], 1.0))])
            for s_ in sd[1:]:
                tt[ds >= s_] += RNG.uniform(10, 40) * (1.5 if fac[h] < 0.7 else 1)
            xs = np.concatenate([ds, [poly.length]])
            ts = np.concatenate([tt, [tt[-1] + 20]])
            T = ts[-1]
            pt = np.cumsum(RNG.uniform(8, 25, int(T / 8) + 5))
            pt = pt[pt < T]
            if RNG.random() < 0.1:
                g0 = RNG.uniform(0.2, 0.8) * T
                pt = pt[(pt < g0) | (pt > g0 + RNG.uniform(240, 420))]
            pt = pt[RNG.random(len(pt)) > 0.04]
            dist = np.interp(pt, ts, xs)
            la, ln = poly.point_at(dist)
            la = la + RNG.normal(0, 12, len(la)) / 110540.0
            ln = ln + RNG.normal(0, 12, len(ln)) / 111000.0
            jump = RNG.random(len(la)) < 0.003
            la = np.where(jump, la + 0.01, la)
            spd = np.gradient(dist, pt) * 3.6 if len(pt) > 2 else np.zeros(len(pt))
            veh = f"DL1PC{rid}{d}{veh_n % 400:03d}"
            for k in range(len(pt)):
                ts_ = int(t0 + pt[k])
                rows.append((veh, str(la[k]), str(ln[k]), float(abs(spd[k])), float(rid), f"T{rid}{d}{veh_n}",
                             str(ts_), date,
                             dt.datetime.fromtimestamp(ts_, IST).strftime("%H:%M:%S")))
                if RNG.random() < 0.02:
                    rows.append(rows[-1])
            t0 += hw * np.exp(RNG.normal(0, 0.3))
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE vehicle_feed (vehicle_id TEXT, lat TEXT, lng TEXT, speed REAL, route_id REAL, "
                "trip_id TEXT, timestamp TEXT, date DATE, time TEXT)")
    con.executemany("INSERT INTO vehicle_feed VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="synthetic")
    ap.add_argument("--days", type=int, default=10)
    a = ap.parse_args()
    rs, sh = build_static(os.path.join(a.out, "data"))
    for i in range(a.days):
        date = (dt.date(2026, 10, 1) + dt.timedelta(days=i)).isoformat()
        n = simulate_day(date, rs, sh, os.path.join(a.out, f"bus_movements_{date.replace('-', '_')}.db"))
        print(date, n, "rows")


if __name__ == "__main__":
    main()

"""Builds the OFFLINE PACK (data/pack.json) that lets the screen plan a trip with no server and no internet.

The pack is a small summary of the full model, for the routes that have bus-GPS history:
  for every route direction, day type (weekday/weekend) and hour:
    - minutes between each pair of neighbouring stops (typical),
    - how much a ride can vary (20 quantile ratios, for long and for short rides),
    - how long a rider waits for a bus (20 quantiles),
    - confidence label and the level of the fallback ladder.
It also writes data/pack_check.json: 400 random trips with the FULL model's answer, so the page /selftest.html
can show how closely the offline planner agrees with the server.

    python -m leavewise.export_pack --data data --passes passes
"""
import argparse
import datetime as dt
import json
import os
import numpy as np
from .model import Model
from .common import IST, HOLIDAYS

DATES = {"weekday": (2026, 10, 7), "weekend": (2026, 10, 10)}
LONG_MIN = 6      # a ride shorter than this many stops uses the "short ride" spread


NQ = 20


def q11(a):      # 20 quantiles taken at the middle of 20 equal slices (2.5%, 7.5% ... 97.5%): no extreme tails
    return [float(x) for x in np.percentile(a, (np.arange(NQ) + 0.5) * 100.0 / NQ)]


def build(m, check_n=400, seed=11):
    pack = dict(version=1, holidays=sorted(HOLIDAYS), buffer=m.buffer, calib=m.calib, routes=[])
    rng = np.random.default_rng(seed)
    meta = []
    for (r, d) in sorted(m.modelled):
        if (r, d) not in m.groups:
            continue
        st = m._rd[(r, d)]
        n = len(st)
        if n < 10:
            continue
        a, b = n // 4, (3 * n) // 4
        c = n // 2
        short = (max(0, c - 3), min(n - 1, c + 3))
        entry = dict(id=r, dir=int(d), name=str(st.short_name.iloc[0]), span=b - a,
                     stops=[[s.stop_id, s.stop_name, round(float(s.lat), 5), round(float(s.lng), 5)] for s in st.itertuples()],
                     tab={})
        for dtype, (y, mo, dd) in DATES.items():
            hours = []
            for h in range(24):
                seg = np.array([float(np.median(m.ride_samples(r, d, k, k + 1, h, dtype)[0])) for k in range(n - 1)])
                ra = m.ride_samples(r, d, a, b, h, dtype)[0]
                med = float(np.median(ra))
                tot = seg[a:b].sum()
                if tot > 0 and med > 0:
                    seg = seg * (med / tot)     # make the neighbour-to-neighbour pieces add up to the measured long-ride median
                rs = m.ride_samples(r, d, short[0], short[1], h, dtype)[0]
                w, _ = m.wait_samples(r, d, c, h, dtype)
                e = m.estimate(r, d, a, b, dt.datetime(y, mo, dd, h, 0, tzinfo=IST))
                hours.append(dict(
                    seg=[int(round(x)) for x in seg],
                    rl=[round(x / med, 3) for x in q11(ra)] if med > 0 else [1] * NQ,
                    rs=[round(x / max(1.0, float(np.median(rs))), 3) for x in q11(rs)],
                    w=[int(round(x)) for x in q11(w)], c=e["confidence"], n=int(e["n_observations"]), lv=int(e["level"])))
            entry["tab"][dtype] = hours
        pack["routes"].append(entry)
        meta.append((r, d, n))
    # random trips with the full model's answer, for the self-test page
    checks = []
    for _ in range(check_n):
        r, d, n = meta[int(rng.integers(len(meta)))]
        i = int(rng.integers(0, n - 4))
        j = int(rng.integers(i + 3, min(n, i + 40)))
        dtype = ["weekday", "weekend"][int(rng.integers(2))]
        h = int(rng.choice([8, 13, 18, 22]))
        y, mo, dd = DATES[dtype]
        e = m.estimate(r, d, i, j, dt.datetime(y, mo, dd, h, int(rng.integers(0, 60)), tzinfo=IST))
        checks.append(dict(r=r, d=d, i=i, j=j, dtype=dtype, h=h, ride=e["ride_typical_min"], plan=e["total_plan_min"], typ=e["total_typical_min"]))
    return pack, checks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="data")
    ap.add_argument("--passes", default="passes")
    a = ap.parse_args()
    m = Model(a.data, a.passes)
    pack, checks = build(m)
    p = os.path.join(a.data, "pack.json")
    json.dump(pack, open(p, "w"), separators=(",", ":"))
    json.dump(checks, open(os.path.join(a.data, "pack_check.json"), "w"), separators=(",", ":"))
    print(f"pack: {len(pack['routes'])} route directions, {os.path.getsize(p) / 1024:.0f} KB -> {p}")


if __name__ == "__main__":
    main()

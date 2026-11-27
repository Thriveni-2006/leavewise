"""City pulse: how much slower/faster Delhi's buses are than their timetable, by hour and day type.

Learned from the real stop pass times of the modelled routes. It lets us give an honest ESTIMATE for routes
we have no GPS history for: timetable time x city pulse. `python -m leavewise.pulse` also runs a
hide-one-route test: for each route, learn the pulse from the OTHER routes only and see if it predicts
that route better than the plain timetable.
"""
import argparse, glob, json, os
import numpy as np
import pandas as pd
from .common import IST, daytype_of_date
import datetime as dt


def load_ratios(data_dir, passes_dir, pairs_per_run=6, seed=0):
    rs = pd.read_csv(os.path.join(data_dir, "route_stops.csv"), dtype={"route_id": str, "stop_id": str})
    sched = {k: g.sort_values("seq").sched_sec.values for k, g in rs.groupby(["route_id", "direction"])}
    rng = np.random.default_rng(seed)
    rows = []
    for f in sorted(glob.glob(os.path.join(passes_dir, "passes_*.csv"))):
        p = pd.read_csv(f, dtype={"route_id": str, "stop_id": str})
        for (r, d), g in p.groupby(["route_id", "direction"]):
            s = sched.get((r, int(d)))
            if s is None or np.isnan(s).any():
                continue
            wide = g.pivot_table(index="run_id", columns="seq", values="t", aggfunc="first")
            cols = wide.columns.values.astype(int)
            for run, row in wide.iterrows():
                t = row.values
                ok = np.where(np.isfinite(t))[0]
                if len(ok) < 4:
                    continue
                for _ in range(pairs_per_run):
                    a, b = np.sort(rng.choice(len(ok), 2, replace=False))
                    i, j = cols[ok[a]], cols[ok[b]]
                    act = t[ok[b]] - t[ok[a]]
                    sc = s[j] - s[i]
                    if act <= 0 or sc <= 60 or j >= len(s):
                        continue
                    ts = dt.datetime.fromtimestamp(t[ok[a]], IST)
                    rows.append((r, d, ts.hour, daytype_of_date(g.date.iloc[0]), act, sc))
    return pd.DataFrame(rows, columns=["route", "dir", "hour", "dtype", "actual", "sched"])


QS = [round(q, 2) for q in np.arange(0.05, 0.96, 0.05)]   # 19 quantiles: 5% .. 95%


def pulse_table(df):
    """actual/timetable ratio quantiles per day type and hour (+-1 h window; falls back to the day-type pool)."""
    df = df.assign(ratio=df.actual / df.sched)
    out = {}
    for dtp, g in df.groupby("dtype"):
        pool = [float(x) for x in np.quantile(g.ratio, QS)]
        tab = []
        for h in range(24):
            w = g[g.hour.isin([(h - 1) % 24, h, (h + 1) % 24])]
            tab.append([float(x) for x in np.quantile(w.ratio, QS)] if len(w) >= 60 else pool)
        out[dtp] = tab
    return out


def hide_one_route(df):
    rows = []
    for r in sorted(df.route.unique()):
        te, tr = df[df.route == r], df[df.route != r]
        if len(te) < 50 or len(tr) < 200:
            continue
        tab = pulse_table(tr)
        default = next(iter(tab.values()))
        q = np.array([tab.get(d, default)[h] for d, h in zip(te.dtype, te.hour)])   # n x 19
        med, p90 = q[:, 9], q[:, 17]                                                   # 50% and 90%
        e_t = np.mean(np.abs(te.sched - te.actual)) / 60
        e_p = np.mean(np.abs(te.sched * med - te.actual)) / 60
        cov = float(np.mean(te.actual.values <= te.sched.values * p90))
        cov_t = float(np.mean(te.actual.values <= te.sched.values))
        rows.append(dict(route=r, n=len(te), timetable_mae=round(e_t, 2), pulse_mae=round(e_p, 2),
                         p90_covers=round(cov * 100), timetable_covers=round(cov_t * 100)))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="data"); ap.add_argument("--passes", default="passes")
    ap.add_argument("--out", default="data/pulse.json")
    a = ap.parse_args()
    df = load_ratios(a.data, a.passes)
    tab = pulse_table(df)
    json.dump(tab, open(a.out, "w"))
    t = hide_one_route(df)
    if len(t):
        print(t.to_string(index=False))
        better = int((t.pulse_mae < t.timetable_mae).sum())
        print(f"\nHide-one-route test: pulse beat the plain timetable on {better} of {len(t)} routes. "
              f"Mean error {t.timetable_mae.mean():.2f} -> {t.pulse_mae.mean():.2f} min. "
              f"The pulse's 90% time was enough for {t.p90_covers.mean():.0f}% of trips (timetable alone: {t.timetable_covers.mean():.0f}%).")
        t.to_csv("pulse_hide_one_route.csv", index=False)
    for k, v in tab.items():
        print(k, "median pulse (x timetable) by hour:", [round(x[9], 2) for x in v])


if __name__ == "__main__":
    main()

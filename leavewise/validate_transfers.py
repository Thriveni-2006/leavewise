"""Transfer validation: do journeys with ONE change of bus hold up against what buses really did?

On a hold-out day that the model never saw, we play out real two-bus trips:
  a rider arrives at the first stop at a random moment -> takes the next bus that really came on route 1 ->
  gets off at the change stop -> walks -> waits for the next bus that really came on route 2 -> rides to the end.
The actual door-to-door minutes are compared with what the planner (`Model._transfer_options`) said.

    python -m leavewise.validate_transfers --data data --passes passes --holdout 2026-10-14 --out transfer_report.md
"""
import argparse
import datetime as dt
import os
import numpy as np
import pandas as pd
from .common import IST, daytype_of_date, haversine_m
from .model import Model, WALK_DETOUR, WALK_SPEED_MS

HOURS = [8, 13, 18, 22]


def candidate_pairs(m, max_walk=300, min_stops=3, seed=0, cap=400):
    """(rd1, i, k, rd2, mm, j): ride route 1 from stop i to k, walk to route 2's stop mm, ride on to j. Modelled routes only."""
    rng = np.random.default_rng(seed)
    rds = sorted(m.modelled)
    cell = {}
    for rd in rds:
        g = m._rd[rd]
        for p in range(len(g)):
            cell.setdefault(m._cell(g.lat.values[p], g.lng.values[p]), []).append((rd, p))
    out = []
    for rd1 in rds:
        g1 = m._rd[rd1]
        for k in range(min_stops, len(g1) - 1):
            ci, cj = m._cell(g1.lat.values[k], g1.lng.values[k])
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for rd2, mm in cell.get((ci + di, cj + dj), ()):
                        if rd2[0] == rd1[0]:
                            continue
                        g2 = m._rd[rd2]
                        if mm >= len(g2) - min_stops - 1:
                            continue
                        if haversine_m(g1.lat.values[k], g1.lng.values[k], g2.lat.values[mm], g2.lng.values[mm]) > max_walk:
                            continue
                        out.append((rd1, k, rd2, mm))
    rng.shuffle(out)
    res, seen = [], set()
    for rd1, k, rd2, mm in out:
        if (rd1, rd2) in seen:                     # at most one change point per pair of routes at first, then fill up
            continue
        seen.add((rd1, rd2))
        g1, g2 = m._rd[rd1], m._rd[rd2]
        i = max(0, k - int(rng.integers(min_stops, min(12, k) + 1)))
        j = min(len(g2) - 1, mm + int(rng.integers(min_stops, 13)))
        res.append((rd1, i, k, rd2, mm, j))
        if len(res) >= cap:
            break
    return res


def run(data, passes, holdout, out_md, n_per_hour=3, seed=3, cap=250):
    full = Model(data, passes)
    m = Model(data, passes, exclude_dates=[holdout])       # never saw the hold-out day; same learned safety buffer as the app
    rng = np.random.default_rng(seed)
    rows = []
    cands = candidate_pairs(m, cap=cap)
    for (rd1, i, k, rd2, mm, j) in cands:
        g1, g2 = full.groups.get(rd1), full.groups.get(rd2)
        if g1 is None or g2 is None:
            continue
        s1, s2 = g1["dates"] == holdout, g2["dates"] == holdout
        if s1.sum() < 5 or s2.sum() < 5:
            continue
        T1, W1, T2, W2 = g1["T"][s1], g1["Tw"][s1], g2["T"][s2], g2["Tw"][s2]
        a_id, b_id = m._rd[rd1].stop_id.values[i], m._rd[rd2].stop_id.values[j]
        for h in HOURS:
            lo = dt.datetime.fromisoformat(holdout).replace(tzinfo=IST, hour=h).timestamp()
            for arr in lo + rng.uniform(0, 3600, n_per_hour):
                when = dt.datetime.fromtimestamp(arr, IST)
                opts = m._transfer_options({a_id: 0.0}, {b_id: 0.0}, when, "depart", keep_n=12)
                o = next((x for x in opts if x["legs"][0]["route_id"] == rd1[0] and x["legs"][1]["route_id"] == rd2[0]
                          and all(l["measured"] for l in x["legs"])), None)
                if o is None:
                    continue
                l1, l2 = o["legs"]
                i1, k1, m2, j2 = l1["board_idx"], l1["alight_idx"], l2["board_idx"], l2["alight_idx"]
                walk = o["transfer_walk_min"] * 60.0
                nxt = np.where(np.isfinite(W1[:, i1]) & (W1[:, i1] >= arr))[0]
                if len(nxt) == 0:
                    continue
                a = nxt[np.argmin(W1[nxt, i1])]
                if not np.isfinite(T1[a, k1]) or W1[a, i1] - arr > 3 * 3600:
                    continue
                arr2 = T1[a, k1] + walk
                nx2 = np.where(np.isfinite(W2[:, m2]) & (W2[:, m2] >= arr2))[0]
                if len(nx2) == 0:
                    continue
                b = nx2[np.argmin(W2[nx2, m2])]
                if not np.isfinite(T2[b, j2]) or W2[b, m2] - arr2 > 3 * 3600:
                    continue
                actual = (T2[b, j2] - arr) / 60.0
                rows.append(dict(r1=l1["route"], r2=l2["route"], hour=h, actual=actual, typical=o["total_typical_min"],
                                 plan=o["total_plan_min"], raw=o["total_plan_raw_min"], conf=o["confidence"],
                                 wait1=(W1[a, i1] - arr) / 60, wait2=(W2[b, m2] - arr2) / 60))
    df = pd.DataFrame(rows)
    if df.empty:
        txt = "# Transfer validation\n\nNo two-bus trips with GPS history on both routes were found on the hold-out day.\n"
        open(out_md, "w", encoding="utf-8").write(txt)
        print(txt)
        return df
    df.to_csv(os.path.splitext(out_md)[0] + "_rows.csv", index=False)
    cov = lambda c: float((df.actual <= df[c]).mean() * 100)
    L = ["# Pahunch transfer validation", "",
         f"Test day (never seen by the model): **{holdout}** ({daytype_of_date(holdout)}). "
         f"{len(df)} simulated trips with one change of bus, {df[['r1','r2']].drop_duplicates().shape[0]} route pairs, hours {HOURS}.",
         "Each trip: next real bus on route 1, then the next real bus on route 2 after walking between the stops.", "",
         "## Was the plan-for time enough? (target 90%)", "",
         f"Before the buffer: **{cov('raw'):.0f}%**. After the buffer (what the app uses): **{cov('plan'):.0f}%**. Typical time alone: {cov('typical'):.0f}%.",
         f"Typical-time error: {float((df.typical - df.actual).abs().mean()):.1f} min on average (trips take {float(df.actual.mean()):.0f} min on average).",
         f"Cost of caution: you arrive **{float((df.plan - df.actual).mean()):.0f} min** earlier than needed on average (median {float((df.plan - df.actual).median()):.0f} min).", "",
         "| Confidence | Trips | Plan-for was enough |", "|---|---|---|"]
    for c in ["high", "medium", "low"]:
        x = df[df.conf == c]
        if len(x):
            L.append(f"| {c} | {len(x)} | {float((x.actual <= x.plan).mean() * 100):.0f}% |")
    L += ["", "| Hour | Trips | Plan-for was enough |", "|---|---|---|"]
    for h in HOURS:
        x = df[df.hour == h]
        if len(x):
            L.append(f"| {h}:00 | {len(x)} | {float((x.actual <= x.plan).mean() * 100):.0f}% |")
    L += ["", "## Limits", "",
          "- Only pairs of routes that BOTH have GPS history are tested. Changes involving timetable-only routes are marked 'estimated' in the app and are not part of this test.",
          "- Walking between stops uses the planner's own walking estimate (no GPS on foot).",
          "- One test day. Re-run with `--holdout` for other days and report the range."]
    txt = "\n".join(L) + "\n"
    open(out_md, "w", encoding="utf-8").write(txt)
    print(txt)
    return df


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="data")
    ap.add_argument("--passes", default="passes")
    ap.add_argument("--holdout", required=True)
    ap.add_argument("--out", default="transfer_report.md")
    ap.add_argument("--cap", type=int, default=250)
    a = ap.parse_args()
    run(a.data, a.passes, a.holdout, a.out, cap=a.cap)


if __name__ == "__main__":
    main()

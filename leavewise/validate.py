"""Step 4: honest validation on a held-out day (the model never sees it).

Reports: error of typical ride time vs timetable and vs all-day average, whether the 'plan-for'
time is really enough ~9 times in 10, and whether HIGH confidence is really more reliable than LOW.
Also writes data/calibration.json (used by the app) and a Google Maps comparison template.
"""
import argparse
import datetime as dt
import json
import os
import numpy as np
import pandas as pd
from .common import IST, daytype_of_date, hour_of
from .model import Model

HOURS = [8, 13, 18, 22]


def pick_holdout(passes_dir):
    import glob
    dates = sorted(os.path.basename(f)[7:17] for f in glob.glob(os.path.join(passes_dir, "passes_*.csv")))
    wk = [d for d in dates if daytype_of_date(d) == "weekday"]
    return wk[-1] if wk else dates[-1]


def collect(m, full, day, seed=1, live_w=None):
    """Simulate riders on `day`: arrive at a stop at a random moment, take the next bus that really came."""
    rows = []
    rng = np.random.default_rng(seed)
    dtype = daytype_of_date(day)
    for (r, d), g in full.groups.items():
        sel = g["dates"] == day
        if sel.sum() < 5:
            continue
        T = g["T"][sel]
        Tw = g["Tw"][sel]
        n = T.shape[1]
        st = g["stops"]
        pairs = sorted({(i, j) for i in range(0, n - 4, 3) for j in (i + 4, i + 9, n - 1) if j < n})
        for i, j in pairs:
            seen_i = np.isfinite(Tw[:, i])
            if seen_i.sum() < 3:
                continue
            sched = st.sched_sec.values[j] - st.sched_sec.values[i] if st.sched_sec.notna().all() else np.nan
            ride_all, _, _ = m.ride_samples(r, d, i, j, 99, dtype)
            for h in HOURS:
                lo = dt.datetime.fromisoformat(day).replace(tzinfo=IST, hour=h).timestamp()
                for arr in lo + rng.uniform(0, 3600, 6):
                    nxt = np.where(seen_i & (Tw[:, i] >= arr))[0]
                    if len(nxt) == 0:
                        continue
                    k = nxt[np.argmin(Tw[nxt, i])]
                    if not np.isfinite(T[k, j]):
                        continue
                    wait = Tw[k, i] - arr
                    if wait > 3 * 3600:
                        continue
                    ride = T[k, j] - Tw[k, i]
                    when = dt.datetime.fromtimestamp(arr, IST)
                    e = m.estimate(r, d, i, j, when)
                    e1 = m.estimate(r, d, i, j, when, live=(Tw, live_w, True)) if live_w is not None else e
                    rows.append(dict(live_rho=e1["live_rho"], live_n=e1["live_n"], live_ride_median=e1["ride_typical_min"],
                                     live_total_plan=e1["total_plan_min"], live_ride_p90=e1["ride_plan_min"],
                                     live_total_raw=e1["total_plan_raw_min"], live_total_typ=e1["total_typical_min"],
                                     last_ago=e1["last_bus_ago_min"], pred_wait_med=e["wait_typical_min"],
                                     live_wait_med=e1["wait_typical_min"], live_wait_p90=e1["wait_plan_min"],route=r, dir=d, i=i, j=j, hour=h, wait_min=wait / 60, ride_min=ride / 60,
                                     actual_total_min=(wait + ride) / 60,
                                     pred_ride_median=e["ride_typical_min"], pred_ride_p90=e["ride_plan_min"],
                                     pred_wait_p90=e["wait_plan_min"], pred_total_typ=e["total_typical_min"],
                                     pred_total_plan=e["total_plan_min"], pred_total_raw=e["total_plan_raw_min"],
                                     conf=e["confidence"], level=e["level"],
                                     sched_min=sched / 60, allday_mean_min=float(np.mean(ride_all)) / 60))
    return pd.DataFrame(rows)


def run(data, passes, holdout, out_md, calib_day=None, use_live=True):
    import glob
    dates = sorted(os.path.basename(f)[7:17] for f in glob.glob(os.path.join(passes, "passes_*.csv")))
    wk = [d for d in dates if daytype_of_date(d) == "weekday" and d < holdout]
    calib_day = calib_day or (wk[-1] if wk else None)
    full = Model(data, passes)
    dtype = daytype_of_date(holdout)
    scale, scale_live = {}, {}
    MAX_BUFFER = 20.0   # never add more than 20 minutes of safety margin
    if calib_day:
        # 1) learn the extra buffer (minutes) to add to the plan-for time, using a day that is NOT the test day
        m_cal = Model(data, passes, exclude_dates=[holdout, calib_day])
        dfc = collect(m_cal, full, calib_day, seed=5, live_w=1.0 if use_live else None)
        # choose how much to trust the live check, using the calibration day only
        have = dfc.dropna(subset=["live_rho"])
        best_w, best = 0.0, float(np.mean(np.abs(dfc.pred_ride_median - dfc.ride_min)))
        for w_ in (0.25, 0.5, 0.75, 1.0):
            adj = dfc.pred_ride_median * np.where(dfc.live_rho.notna(), np.clip(1 + w_ * (dfc.live_rho.fillna(1) - 1), 0.7, 1.6), 1.0)
            err = float(np.mean(np.abs(adj - dfc.ride_min)))
            if err < best - 1e-9:
                best_w, best = w_, err
        for c in ["high", "medium", "low"]:
            x = dfc[dfc.conf == c]
            if len(x) >= 30:
                gap = x.actual_total_min - x.pred_total_raw
                scale[c] = round(float(np.clip(np.quantile(gap, 0.9), 0.0, MAX_BUFFER)), 1)
        scale_live = {}
        for c in ["high", "medium", "low"]:
            x = dfc[dfc.conf == c]
            if len(x) >= 30:
                gap = x.actual_total_min - x.live_total_raw
                scale_live[c] = round(float(np.clip(np.quantile(gap, 0.9), 0.0, MAX_BUFFER)), 1)
    # 2) test on the hold-out day with a model that never saw it
    live_w = locals().get("best_w", 0.5) if use_live else None
    m = Model(data, passes, exclude_dates=[holdout])
    m.buffer, m.buffer_live = scale, scale_live
    df = collect(m, full, holdout, live_w=live_w)
    df.to_csv(os.path.join(os.path.dirname(out_md) or ".", "validation_rows.csv"), index=False)
    if df.empty:
        raise SystemExit("No overlapping trips on the hold-out day. Process more days / pick another hold-out.")
    mae = lambda a, b: float(np.mean(np.abs(a - b)))
    mape = lambda a, b: float(np.mean(np.abs(a - b) / b) * 100)
    cov = lambda x, col: float((x.actual_total_min <= x[col]).mean() * 100)
    lines = [f"# Pahunch validation report", "",
             f"Test day (never seen by the model): **{holdout}** ({dtype}). The extra safety buffer was learned on **{calib_day}**, a different day. "
             f"Model trained on {len(m.dates)} days: {', '.join(m.dates)}.",
             f"Test cases: {len(df)} rider arrivals (random moments inside hours {HOURS}) over {df.route.nunique()} routes.", "",
             "## 1. How good is the typical in-vehicle time?", "",
             "| Method | MAE (min) | MAPE (%) |", "|---|---|---|",
             f"| Pahunch (hour + day-type median) | {mae(df.pred_ride_median, df.ride_min):.2f} | {mape(df.pred_ride_median, df.ride_min):.1f} |",
             f"| All-day average (no time-of-day) | {mae(df.allday_mean_min, df.ride_min):.2f} | {mape(df.allday_mean_min, df.ride_min):.1f} |"]
    if df.sched_min.notna().any():
        s_ = df.dropna(subset=["sched_min"])
        lines.append(f"| Published timetable | {mae(s_.sched_min, s_.ride_min):.2f} | {mape(s_.sched_min, s_.ride_min):.1f} |")
    lines += ["", "## 2. Is the plan-for time really enough? (wait + ride; target 90%)", "",
              f"Before the buffer: **{cov(df, 'pred_total_raw'):.0f}%**. After adding the buffer (what the app uses): **{cov(df, 'pred_total_plan'):.0f}%**. "
              f"Typical time alone: {cov(df, 'pred_total_typ'):.0f}%.",
              f"Ride part alone, P90 covered: {(df.ride_min <= df.pred_ride_p90).mean() * 100:.0f}%. Wait part alone, P90 covered: {(df.wait_min <= df.pred_wait_p90).mean() * 100:.0f}%.", "",
              f"Extra buffer learned (minutes, capped at {MAX_BUFFER:.0f}): " + (", ".join(f"{k} +{v:.1f}" for k, v in scale.items()) or "none (too few cases)"), "",
              f"Cost of caution: on average you arrive **{float((df.pred_total_plan - df.actual_total_min).mean()):.0f} min** earlier than needed "
              f"(median {float((df.pred_total_plan - df.actual_total_min).median()):.0f} min).", "",
              "| Confidence label | Cases | Plan-for was enough | Ride typical error (min) |", "|---|---|---|---|"]
    on_time = {}
    for c in ["high", "medium", "low"]:
        x = df[df.conf == c]
        if len(x) >= 20:
            rate = float((x.actual_total_min <= x.pred_total_plan).mean())
            on_time[c] = round(rate, 2)
            lines.append(f"| {c} | {len(x)} | {rate * 100:.0f}% | {mae(x.pred_ride_median, x.ride_min):.1f} |")
        else:
            lines.append(f"| {c} | {len(x)} | (too few cases) | |")
    lines += ["", "## 3. By time of day", "", "| Hour | Cases | Ride typical error (min) | Plan-for enough |", "|---|---|---|---|"]
    for h in HOURS:
        x = df[df.hour == h]
        if len(x):
            lines.append(f"| {h}:00 | {len(x)} | {mae(x.pred_ride_median, x.ride_min):.1f} | {cov(x, 'pred_total_plan'):.0f}% |")
    have = df[df.last_ago.notna()] if use_live else df.iloc[0:0]
    wm = lambda c1, c2, x: float(np.mean(np.abs(x[c1] - x[c2])))
    lines += [] if not use_live else ["", "## 4. Live + historical: 'when did the last bus leave my stop?'", "",
              "For each test rider we looked at the buses that had ALREADY passed their boarding stop before they arrived, and asked how long ago the last one left. "
              "The wait is then taken from the gaps that are still possible given that (a bus 2 minutes ago on a 10-minute route means waiting up to 8 more). "
              f"Live data was available for **{len(have) / len(df) * 100:.0f}%** of cases (weight on live ride speed chosen on the calibration day: {live_w}).", "",
              "| | History only | History + live |", "|---|---|---|",
              f"| Wait error, all cases (min) | {wm('pred_wait_med', 'wait_min', df):.2f} | {wm('live_wait_med', 'wait_min', df):.2f} |",
              f"| Total time error (typical, min) | {wm('pred_total_typ', 'actual_total_min', df):.2f} | {wm('live_total_typ', 'actual_total_min', df):.2f} |",
              f"| Plan-for was enough | {cov(df, 'pred_total_plan'):.0f}% | {float((df.actual_total_min <= df.live_total_plan).mean() * 100):.0f}% |",
              f"| Cost of caution (arrive earlier than needed, mean min) | {float((df.pred_total_plan - df.actual_total_min).mean()):.1f} | {float((df.live_total_plan - df.actual_total_min).mean()):.1f} |"]
    if use_live and len(have):
        lines += [f"| ...only cases with live data: wait error (min) | {wm('pred_wait_med', 'wait_min', have):.2f} | {wm('live_wait_med', 'wait_min', have):.2f} |",
                  f"| ...only cases with live data: plan-for enough | {cov(have, 'pred_total_plan'):.0f}% | {float((have.actual_total_min <= have.live_total_plan).mean() * 100):.0f}% |",
                  f"| ...only cases with live data: cost of caution (min) | {float((have.pred_total_plan - have.actual_total_min).mean()):.1f} | {float((have.live_total_plan - have.actual_total_min).mean()):.1f} |"]
    lines += ["", "## 5. Where it is weakest", "",
              f"- Cases that fell to a fallback level (thin data): {(df.level >= 4).mean() * 100:.0f}%.",
              "- One test day is a small test. Re-run with other days (`--holdout`) and report the range.",
              "- Wait time depends on how many bus passes were seen; gaps in GPS make some waits look longer.",
              "- Journeys with transfers and signal-level delay are not validated here. The live check is a replay of a past day (the real feed was not available to us)."]
    open(out_md, "w", encoding="utf-8").write("\n".join(lines))
    json.dump(dict(on_time=on_time, buffer_min=scale, buffer_live_min=scale_live, live_w=(live_w or 0.0)), open(os.path.join(data, "calibration.json"), "w"))
    print("\n".join(lines))
    # Google Maps template
    tpl = []
    for (r, d), g in list(full.groups.items())[:6]:
        st = g["stops"]
        n = len(st)
        for h in (8, 13, 18):
            tpl.append(dict(route=st.short_name.iloc[0], from_stop=st.stop_name.iloc[1], from_lat=st.lat.iloc[1], from_lng=st.lng.iloc[1],
                            to_stop=st.stop_name.iloc[n - 2], to_lat=st.lat.iloc[n - 2], to_lng=st.lng.iloc[n - 2],
                            depart_hour=h, date=holdout, gmaps_minutes=""))
    pd.DataFrame(tpl).to_csv(os.path.join(os.path.dirname(out_md) or ".", "gmaps_template.csv"), index=False)


def compare_gmaps(data, passes, filled):
    m = Model(data, passes)
    g = pd.read_csv(filled)
    g = g.dropna(subset=["gmaps_minutes"])
    out = []
    for r in g.itertuples():
        when = dt.datetime.fromisoformat(r.date).replace(tzinfo=IST, hour=int(r.depart_hour))
        js = m.journeys((r.from_lat, r.from_lng), (r.to_lat, r.to_lng), when, "depart")
        if js:
            out.append(dict(route=r.route, hour=r.depart_hour, gmaps=r.gmaps_minutes, ours=js[0]["door_to_door_typical_min"]))
    o = pd.DataFrame(out)
    if len(o):
        print(o.to_string(index=False))
        print("MAE vs Google Maps (min):", round(float(np.mean(np.abs(o.gmaps - o.ours))), 1))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="data")
    ap.add_argument("--passes", default="passes")
    ap.add_argument("--holdout", default=None, help="YYYY-MM-DD day to hold out (default: last weekday)")
    ap.add_argument("--out", default="validation_report.md")
    ap.add_argument("--no-live", action="store_true", help="skip the live-check experiment (about twice as fast)")
    ap.add_argument("--gmaps", default=None, help="your filled gmaps_template.csv to compare against Google Maps")
    a = ap.parse_args()
    if a.gmaps:
        return compare_gmaps(a.data, a.passes, a.gmaps)
    run(a.data, a.passes, a.holdout or pick_holdout(a.passes), a.out, use_live=not a.no_live)


if __name__ == "__main__":
    main()

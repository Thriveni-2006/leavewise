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


def collect(m, full, day, seed=1):
    """Simulate riders on `day`: arrive at a stop at a random moment, take the next bus that really came."""
    rows = []
    rng = np.random.default_rng(seed)
    dtype = daytype_of_date(day)
    for (r, d), g in full.groups.items():
        sel = g["dates"] == day
        if sel.sum() < 5:
            continue
        T = g["T"][sel]
        n = T.shape[1]
        st = g["stops"]
        pairs = sorted({(i, j) for i in range(0, n - 4, 3) for j in (i + 4, i + 9, n - 1) if j < n})
        for i, j in pairs:
            seen_i = np.isfinite(T[:, i])
            if seen_i.sum() < 3:
                continue
            sched = st.sched_sec.values[j] - st.sched_sec.values[i] if st.sched_sec.notna().all() else np.nan
            ride_all, _, _ = m.ride_samples(r, d, i, j, 99, dtype)
            for h in HOURS:
                lo = dt.datetime.fromisoformat(day).replace(tzinfo=IST, hour=h).timestamp()
                for arr in lo + rng.uniform(0, 3600, 6):
                    nxt = np.where(seen_i & (T[:, i] >= arr))[0]
                    if len(nxt) == 0:
                        continue
                    k = nxt[np.argmin(T[nxt, i])]
                    if not np.isfinite(T[k, j]):
                        continue
                    wait = T[k, i] - arr
                    if wait > 3 * 3600:
                        continue
                    ride = T[k, j] - T[k, i]
                    e = m.estimate(r, d, i, j, dt.datetime.fromtimestamp(arr, IST))
                    rows.append(dict(route=r, dir=d, i=i, j=j, hour=h, wait_min=wait / 60, ride_min=ride / 60,
                                     actual_total_min=(wait + ride) / 60,
                                     pred_ride_median=e["ride_typical_min"], pred_ride_p90=e["ride_plan_min"],
                                     pred_wait_p90=e["wait_plan_min"], pred_total_typ=e["total_typical_min"],
                                     pred_total_plan=e["total_plan_min"], pred_total_raw=e["total_plan_raw_min"],
                                     conf=e["confidence"], level=e["level"],
                                     sched_min=sched / 60, allday_mean_min=float(np.mean(ride_all)) / 60))
    return pd.DataFrame(rows)


def run(data, passes, holdout, out_md, calib_day=None):
    import glob
    dates = sorted(os.path.basename(f)[7:17] for f in glob.glob(os.path.join(passes, "passes_*.csv")))
    wk = [d for d in dates if daytype_of_date(d) == "weekday" and d < holdout]
    calib_day = calib_day or (wk[-1] if wk else None)
    full = Model(data, passes)
    dtype = daytype_of_date(holdout)
    scale = {}
    MAX_BUFFER = 20.0   # never add more than 20 minutes of safety margin
    if calib_day:
        # 1) learn the extra buffer (minutes) to add to the plan-for time, using a day that is NOT the test day
        m_cal = Model(data, passes, exclude_dates=[holdout, calib_day])
        dfc = collect(m_cal, full, calib_day, seed=5)
        for c in ["high", "medium", "low"]:
            x = dfc[dfc.conf == c]
            if len(x) >= 30:
                gap = x.actual_total_min - x.pred_total_raw
                scale[c] = round(float(np.clip(np.quantile(gap, 0.9), 0.0, MAX_BUFFER)), 1)
    # 2) test on the hold-out day with a model that never saw it
    m = Model(data, passes, exclude_dates=[holdout])
    m.buffer = scale
    df = collect(m, full, holdout)
    df.to_csv(os.path.join(os.path.dirname(out_md) or ".", "validation_rows.csv"), index=False)
    if df.empty:
        raise SystemExit("No overlapping trips on the hold-out day. Process more days / pick another hold-out.")
    mae = lambda a, b: float(np.mean(np.abs(a - b)))
    mape = lambda a, b: float(np.mean(np.abs(a - b) / b) * 100)
    cov = lambda x, col: float((x.actual_total_min <= x[col]).mean() * 100)
    lines = [f"# LeaveWise validation report", "",
             f"Test day (never seen by the model): **{holdout}** ({dtype}). The extra safety buffer was learned on **{calib_day}**, a different day. "
             f"Model trained on {len(m.dates)} days: {', '.join(m.dates)}.",
             f"Test cases: {len(df)} rider arrivals (random moments inside hours {HOURS}) over {df.route.nunique()} routes.", "",
             "## 1. How good is the typical in-vehicle time?", "",
             "| Method | MAE (min) | MAPE (%) |", "|---|---|---|",
             f"| LeaveWise (hour + day-type median) | {mae(df.pred_ride_median, df.ride_min):.2f} | {mape(df.pred_ride_median, df.ride_min):.1f} |",
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
    lines += ["", "## 4. Where it is weakest", "",
              f"- Cases that fell to a fallback level (thin data): {(df.level >= 4).mean() * 100:.0f}%.",
              "- One test day is a small test. Re-run with other days (`--holdout`) and report the range.",
              "- Wait time depends on how many bus passes were seen; gaps in GPS make some waits look longer.",
              "- Journeys with transfers, signal-level delay, and live updates are not validated here."]
    open(out_md, "w", encoding="utf-8").write("\n".join(lines))
    json.dump(dict(on_time=on_time, buffer_min=scale), open(os.path.join(data, "calibration.json"), "w"))
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
    ap.add_argument("--gmaps", default=None, help="your filled gmaps_template.csv to compare against Google Maps")
    a = ap.parse_args()
    if a.gmaps:
        return compare_gmaps(a.data, a.passes, a.gmaps)
    run(a.data, a.passes, a.holdout or pick_holdout(a.passes), a.out)


if __name__ == "__main__":
    main()

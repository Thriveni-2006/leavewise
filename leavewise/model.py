"""Step 3: the reliability model + wait-aware leave-by planner.

Everything is learned from stop pass times (passes/*.csv). No black box: medians and percentiles
of what buses actually did, with a fallback ladder when data is thin.
"""
import glob
import json
import os
import datetime as dt
import numpy as np
import pandas as pd
from .common import IST, daytype_of_date, hour_of, haversine_m

WALK_SPEED_MS = 1.25          # 4.5 km/h
WALK_DETOUR = 1.3             # straight line -> real streets
MIN_N = 10                    # minimum observations to trust a ladder level
FALLBACK_SPEED_MS = 14 / 3.6  # last-resort bus speed (14 km/h) if no timetable
ASSUMED_HEADWAY_S = 900


class Model:
    def __init__(self, data_dir="data", passes_dir="passes", exclude_dates=()):
        self.data_dir = data_dir
        self.rs = pd.read_csv(os.path.join(data_dir, "route_stops.csv"),
                              dtype={"route_id": str, "stop_id": str})
        files = sorted(glob.glob(os.path.join(passes_dir, "passes_*.csv")))
        parts = [pd.read_csv(f, dtype={"route_id": str, "stop_id": str}) for f in files]
        p = pd.concat(parts) if parts else pd.DataFrame(columns=["route_id", "direction", "run_id", "seq", "stop_id", "t", "date"])
        p = p[~p.date.isin(set(exclude_dates))]
        self.dates = sorted(p.date.unique())
        self.groups = {}
        for (r, d), g in p.groupby(["route_id", "direction"]):
            wide = g.pivot_table(index="run_id", columns="seq", values="t", aggfunc="first")
            stops = self.rs[(self.rs.route_id == r) & (self.rs.direction == d)].sort_values("seq")
            n = len(stops)
            T = np.full((len(wide), n), np.nan)
            T[:, wide.columns.values.astype(int)] = wide.values
            dates = g.drop_duplicates("run_id").set_index("run_id").loc[wide.index, "date"].values
            self.groups[(r, int(d))] = dict(
                T=T, dates=dates, dtype=np.array([daytype_of_date(x) for x in dates]),
                stops=stops.reset_index(drop=True))
        self.calib, self.buffer = {}, {}
        cp = os.path.join(data_dir, "calibration.json")
        if os.path.exists(cp):
            cj = json.load(open(cp))
            self.calib = cj.get("on_time", {})
            self.buffer = cj.get("buffer_min", {})
        self._stops_all = self.rs.drop_duplicates("stop_id")

    # ---------- raw samples ----------
    def ride_samples(self, r, d, i, j, hour, dtype):
        g = self.groups.get((r, d))
        sched = None
        st = self.rs[(self.rs.route_id == r) & (self.rs.direction == d)].sort_values("seq")
        if len(st) > max(i, j) and st.sched_sec.notna().all():
            sched = float(st.sched_sec.values[j] - st.sched_sec.values[i])
        if g is not None:
            T = g["T"]
            dur = T[:, j] - T[:, i]
            ok = np.isfinite(dur) & (dur > 0)
            hrs = hour_of(np.where(np.isfinite(T[:, i]), T[:, i], 0))
            same_dt = g["dtype"] == dtype
            near = np.isin(hrs, [(hour - 1) % 24, hour, (hour + 1) % 24])
            exact = hrs == hour
            for name, m in (("same hour & day type", ok & same_dt & near),
                            ("same day type, all hours", ok & same_dt),
                            ("all days, all hours", ok)):
                if m.sum() >= MIN_N:
                    lvl = {"same hour & day type": 1, "same day type, all hours": 2, "all days, all hours": 3}[name]
                    return dur[m], lvl, name
        if sched and sched > 0:
            return np.array([sched]), 4, "timetable only"
        dist = float(st.shape_dist.values[j] - st.shape_dist.values[i])
        return np.array([dist / FALLBACK_SPEED_MS]), 5, "distance at 14 km/h"

    def wait_samples(self, r, d, i, hour, dtype):
        """Waits a rider arriving at random would have had. Built from the gaps between consecutive buses
        over the WHOLE day, then keeping arrival moments that fall in this hour window (no cutting of long gaps)."""
        g = self.groups.get((r, d))
        waits = []
        if g is not None:
            T = g["T"][:, i]
            ok = np.isfinite(T)
            for date in np.unique(g["dates"][ok & (g["dtype"] == dtype)]):
                ts = np.sort(T[ok & (g["dates"] == date)])
                for t0, t1 in zip(ts[:-1], ts[1:]):
                    if t1 - t0 < 30 or t1 - t0 > 14400:
                        continue
                    arr = np.arange(t0, t1, 30.0)
                    hh = hour_of(arr)
                    keep = np.isin(hh, [(hour - 1) % 24, hour, (hour + 1) % 24])
                    if keep.any():
                        waits.append(t1 - arr[keep])
        if sum(len(w) for w in waits) >= 100:
            return np.concatenate(waits), "observed gaps between buses"
        return np.linspace(0, ASSUMED_HEADWAY_S, 12), "assumed 15 min gap (little data)"

    # ---------- one trip estimate ----------
    def estimate(self, r, d, i, j, when_ist):
        """when_ist: aware datetime = moment the rider reaches the boarding stop."""
        hour = when_ist.hour
        dtype = daytype_of_date(when_ist.strftime("%Y-%m-%d"))
        ride, lvl, basis = self.ride_samples(r, d, i, j, hour, dtype)
        wait, wbasis = self.wait_samples(r, d, i, hour, dtype)
        rng = np.random.default_rng(7)
        tot = rng.choice(wait, 4000) + rng.choice(ride, 4000)
        n = len(ride) if lvl <= 3 else 0
        p50, p90 = float(np.median(ride)), float(np.percentile(ride, 90))
        spread = p90 / p50 if p50 > 0 else 9
        if lvl == 1 and n >= 30 and spread <= 1.5:
            conf = "high"
        elif lvl <= 2 and n >= MIN_N and spread <= 2.0:
            conf = "medium"
        else:
            conf = "low"
        # "quiet" reference: fastest hour of day with enough data
        quiet = self._quiet(r, d, i, j, dtype)
        buf = self.buffer.get(conf, 0.0)
        raw_plan = float(np.percentile(tot, 90)) / 60
        return dict(
            ride_typical_min=round(p50 / 60, 1), ride_plan_min=round(p90 / 60, 1),
            wait_typical_min=round(float(np.median(wait)) / 60, 1),
            wait_plan_min=round(float(np.percentile(wait, 90)) / 60, 1),
            total_typical_min=round(float(np.median(tot)) / 60, 1),
            total_plan_min=round(raw_plan + buf, 1), total_plan_raw_min=round(raw_plan, 1),
            confidence=conf, n_observations=int(n), basis=basis, wait_basis=wbasis, level=lvl,
            quiet_ride_min=quiet,
            on_time_rate=self.calib.get(conf))

    def _quiet(self, r, d, i, j, dtype):
        g = self.groups.get((r, d))
        if g is None:
            return None
        T = g["T"]
        dur = T[:, j] - T[:, i]
        ok = np.isfinite(dur) & (dur > 0) & (g["dtype"] == dtype)
        hrs = hour_of(np.where(np.isfinite(T[:, i]), T[:, i], 0))
        best = None
        for h in range(24):
            m = ok & (hrs == h)
            if m.sum() >= 5:
                v = float(np.median(dur[m])) / 60
                best = v if best is None else min(best, v)
        return round(best, 1) if best else None

    # ---------- journeys ----------
    def nearby_stops(self, lat, lng, max_m=800):
        s = self._stops_all
        d = np.array([haversine_m(lat, lng, a, b) for a, b in zip(s.lat.values, s.lng.values)])
        out = s.assign(dist=d)
        return out[out.dist <= max_m].sort_values("dist")

    def journeys(self, o, d, when_ist, mode="depart", max_walk=800, top=3):
        """o,d = (lat,lng). mode 'depart': when_ist is leaving time. 'arrive': when_ist is arrive-by."""
        so, sd = self.nearby_stops(*o, max_walk), self.nearby_stops(*d, max_walk)
        if so.empty or sd.empty:
            return []
        walk_o = {r.stop_id: r.dist * WALK_DETOUR / WALK_SPEED_MS for r in so.itertuples()}
        walk_d = {r.stop_id: r.dist * WALK_DETOUR / WALK_SPEED_MS for r in sd.itertuples()}
        options = []
        for (r, dirn), grp in self._all_groups():
            st = grp
            pos = {sid: k for k, sid in enumerate(st.stop_id.values)}
            for a in walk_o:
                for b in walk_d:
                    if a in pos and b in pos and pos[a] < pos[b]:
                        options.append((r, dirn, pos[a], pos[b], a, b, st))
        results = []
        for r, dirn, i, j, a, b, st in options:
            if mode == "depart":
                at_stop = when_ist + dt.timedelta(seconds=walk_o[a])
                e = self.estimate(r, dirn, i, j, at_stop)
            else:
                guess = when_ist - dt.timedelta(minutes=45)
                e = self.estimate(r, dirn, i, j, guess)
                guess = when_ist - dt.timedelta(minutes=e["total_plan_min"]) - dt.timedelta(seconds=walk_o[a] + walk_d[b])
                e = self.estimate(r, dirn, i, j, guess)
            wo, wd = walk_o[a] / 60, walk_d[b] / 60
            res = dict(route=st.short_name.iloc[0], route_id=r, direction=dirn,
                       board_stop=st.stop_name.values[i], board_stop_id=a, board_idx=int(i), alight_idx=int(j),
                       board_lat=float(st.lat.values[i]), board_lng=float(st.lng.values[i]),
                       alight_lat=float(st.lat.values[j]), alight_lng=float(st.lng.values[j]),
                       alight_stop=st.stop_name.values[j], alight_stop_id=b,
                       walk_to_stop_min=round(wo, 1), walk_from_stop_min=round(wd, 1), **e)
            res["door_to_door_typical_min"] = round(wo + wd + e["total_typical_min"], 1)
            res["door_to_door_plan_min"] = round(wo + wd + e["total_plan_min"], 1)
            if mode == "depart":
                res["arrive_typical"] = (when_ist + dt.timedelta(minutes=res["door_to_door_typical_min"])).strftime("%H:%M")
                res["arrive_plan_for"] = (when_ist + dt.timedelta(minutes=res["door_to_door_plan_min"])).strftime("%H:%M")
            else:
                res["leave_by"] = (when_ist - dt.timedelta(minutes=res["door_to_door_plan_min"])).strftime("%H:%M")
                res["leave_typical"] = (when_ist - dt.timedelta(minutes=res["door_to_door_typical_min"])).strftime("%H:%M")
            results.append(res)
        results.sort(key=lambda x: x["door_to_door_plan_min"])
        seen, out = set(), []
        for x in results:
            k = (x["route_id"], x["direction"])
            if k not in seen:
                seen.add(k)
                out.append(x)
        return out[:top]

    def _all_groups(self):
        for (r, d), g in self.rs.groupby(["route_id", "direction"]):
            yield (r, int(d)), g.sort_values("seq").reset_index(drop=True)

    def reliability_table(self, dtype="weekday"):
        """For operators/planners: per route and hour, typical vs plan-for time over the middle 80% of the route.
        index = P90 / median (1.0 = perfectly steady; higher = less predictable)."""
        rows = []
        for (r, d), g in self.groups.items():
            T = g["T"]
            n = T.shape[1]
            i, j = int(n * 0.1), int(n * 0.9)
            if j - i < 5:
                continue
            st = g["stops"]
            dur = T[:, j] - T[:, i]
            ok = np.isfinite(dur) & (dur > 0) & (g["dtype"] == dtype)
            hrs = hour_of(np.where(np.isfinite(T[:, i]), T[:, i], 0))
            for h in range(5, 24):
                m = ok & (hrs == h)
                if m.sum() >= 5:
                    med, p90 = float(np.median(dur[m])) / 60, float(np.percentile(dur[m], 90)) / 60
                    rows.append(dict(route=str(st.short_name.iloc[0]), route_id=r, direction=int(d), hour=h, n=int(m.sum()),
                                     median_min=round(med, 1), p90_min=round(p90, 1), index=round(p90 / med, 2),
                                     from_stop=st.stop_name.iloc[i], to_stop=st.stop_name.iloc[j]))
        return rows

    def hourly_card(self, r, d, i, j, dtype):
        """Plan-for table for a printable stop card: leave-by buffer for every hour."""
        rows = []
        for h in range(5, 24):
            when = dt.datetime(2026, 10, 7 if dtype == "weekday" else 10, h, 0, tzinfo=IST)
            e = self.estimate(r, d, i, j, when)
            rows.append(dict(hour=h, typical=e["total_typical_min"], plan=e["total_plan_min"], confidence=e["confidence"]))
        return rows

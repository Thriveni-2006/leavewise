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


def impute_passes(T, max_gap_stops=3, max_span_s=900):
    """A bus whose GPS missed one stop is still a bus that passed it. Fill a missing stop time by interpolating between
    the nearest observed stops on the same run (at most 3 stops either side, at most 15 min apart). Used for WAIT
    calculations only: without it, missed passes make the gaps between buses look longer than they really were."""
    out = T.copy()
    n = T.shape[1]
    idx = np.arange(n)
    for r in range(T.shape[0]):
        row = T[r]
        ok = np.isfinite(row)
        if ok.sum() < 2:
            continue
        fi = idx[ok]
        miss = idx[(~ok) & (idx > fi[0]) & (idx < fi[-1])]
        if miss.size == 0:
            continue
        pos = np.searchsorted(fi, miss)
        left, right = fi[pos - 1], fi[pos]
        span = row[right] - row[left]
        good = ((miss - left) <= max_gap_stops) & ((right - miss) <= max_gap_stops) & (span > 0) & (span <= max_span_s)
        if good.any():
            m, l, rr = miss[good], left[good], right[good]
            out[r, m] = row[l] + (row[rr] - row[l]) * (m - l) / (rr - l)
    return out


class Model:
    def __init__(self, data_dir="data", passes_dir="passes", exclude_dates=()):
        self.data_dir = data_dir
        self.rs = pd.read_csv(os.path.join(data_dir, "route_stops.csv"),
                              dtype={"route_id": str, "stop_id": str})
        self.modelled = {(r, int(d)) for r, d in self.rs[["route_id", "direction"]].drop_duplicates().values}
        # whole-network layer (every GTFS route): used for routes with no GPS history
        netp = os.path.join(data_dir, "network_stops.csv")
        if os.path.exists(netp):
            net = pd.read_csv(netp, dtype={"route_id": str, "stop_id": str})
            keep = [(r, int(d)) not in self.modelled for r, d in zip(net.route_id.values, net.direction.values)]
            self.rs = pd.concat([self.rs, net[keep]], ignore_index=True)
        self.pulse = {}
        pp = os.path.join(data_dir, "pulse.json")
        if os.path.exists(pp):
            self.pulse = json.load(open(pp))
        self.headway = {}
        self._hw = {}
        hp = os.path.join(data_dir, "headways.csv")
        if os.path.exists(hp):
            hw = pd.read_csv(hp, dtype={"route_id": str})
            for r, d, dtp, h, n in hw[["route_id", "direction", "daytype", "hour", "trips"]].values:
                self.headway[(r, int(d), dtp, int(h))] = int(n)
                self._hw.setdefault((r, int(d), dtp), {})[int(h)] = int(n)
        self._rd = {(r, int(d)): g.sort_values("seq").reset_index(drop=True) for (r, d), g in self.rs.groupby(["route_id", "direction"])}
        self._stop_idx = {}
        for k, g in self._rd.items():
            for pos, sid in enumerate(g.stop_id.values):
                self._stop_idx.setdefault(sid, []).append((k, pos))
        files = sorted(glob.glob(os.path.join(passes_dir, "passes_*.csv")))
        parts = [pd.read_csv(f, dtype={"route_id": str, "stop_id": str}) for f in files]
        p = pd.concat(parts) if parts else pd.DataFrame(columns=["route_id", "direction", "run_id", "seq", "stop_id", "t", "date"])
        p = p[~p.date.isin(set(exclude_dates))]
        self.dates = sorted(p.date.unique())
        self.groups = {}
        for (r, d), g in p.groupby(["route_id", "direction"]):
            wide = g.pivot_table(index="run_id", columns="seq", values="t", aggfunc="first")
            stops = self.rs[(self.rs.route_id == r) & (self.rs.direction == d)].sort_values("seq")
            if stops.empty:
                continue
            n = len(stops)
            T = np.full((len(wide), n), np.nan)
            T[:, wide.columns.values.astype(int)] = wide.values
            dates = g.drop_duplicates("run_id").set_index("run_id").loc[wide.index, "date"].values
            self.groups[(r, int(d))] = dict(
                T=T, Tw=impute_passes(T), dates=dates, dtype=np.array([daytype_of_date(x) for x in dates]),
                stops=stops.reset_index(drop=True))
        self.calib, self.buffer, self.buffer_live = {}, {}, {}
        self.live_w = 0.5
        cp = os.path.join(data_dir, "calibration.json")
        if os.path.exists(cp):
            cj = json.load(open(cp))
            self.calib = cj.get("on_time", {})
            self.buffer = cj.get("buffer_min", {})
            self.buffer_live = cj.get("buffer_live_min", {})
            lw = cj.get("live_w")
            self.live_w = float(lw) if lw is not None else 0.0
        self._stops_all = self.rs.drop_duplicates("stop_id")

    # ---------- raw samples ----------
    def ride_samples(self, r, d, i, j, hour, dtype):
        g = self.groups.get((r, d))
        sched = None
        st = self._rd[(r, d)]
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
            tab = self.pulse.get(dtype) or next(iter(self.pulse.values()), None)
            if tab:
                q = np.array(tab[hour]) if 0 <= hour < 24 else np.mean(np.array(tab), axis=0)   # hour 99 = "all-day average"
                return sched * q, 4, "timetable x city pulse (no GPS history for this route)"
            return np.array([sched]), 4, "timetable only"
        dist = float(st.shape_dist.values[j] - st.shape_dist.values[i])
        return np.array([dist / FALLBACK_SPEED_MS]), 5, "distance at 14 km/h"

    def wait_samples(self, r, d, i, hour, dtype, since_last=None):
        """Waits a rider arriving at random would have had. Built from the gaps between consecutive buses
        over the WHOLE day, then keeping arrival moments that fall in this hour window (no cutting of long gaps)."""
        g = self.groups.get((r, d))
        waits, gaps = [], []
        if g is not None:
            T = g["Tw"][:, i]
            ok = np.isfinite(T)
            for date in np.unique(g["dates"][ok & (g["dtype"] == dtype)]):
                ts = np.sort(T[ok & (g["dates"] == date)])
                for t0, t1 in zip(ts[:-1], ts[1:]):
                    if t1 - t0 < 30 or t1 - t0 > 14400:
                        continue
                    if int(hour_of(np.array([t0]))[0]) in ((hour - 1) % 24, hour, (hour + 1) % 24):
                        gaps.append(t1 - t0)
                    arr = np.arange(t0, t1, 30.0)
                    hh = hour_of(arr)
                    keep = np.isin(hh, [(hour - 1) % 24, hour, (hour + 1) % 24])
                    if keep.any():
                        waits.append(t1 - arr[keep])
        if since_last is not None and len(gaps) >= 30:
            ga = np.array(gaps)
            pool = ga[ga > since_last] - since_last      # the next bus is due: waits left, given the last bus left `since_last` ago
            if len(pool) >= 30:
                return pool, f"observed gaps, given the last bus left {round(since_last / 60)} min ago"
        if sum(len(w) for w in waits) >= 100:
            return np.concatenate(waits), "observed gaps between buses"
        hw = self._hw.get((r, d, dtype))
        note = ""
        if not hw:      # no schedule for this day type: use the other one rather than invent a gap
            other = "weekend" if dtype == "weekday" else "weekday"
            hw = self._hw.get((r, d, other))
            note = f" ({other} schedule)" if hw else ""
        if hw:
            trips = sum(hw.get((hour + k) % 24, 0) for k in (-1, 0, 1)) / 3.0
            if trips >= 0.5:
                return np.linspace(0, 3600.0 / trips, 24), f"scheduled gap (about {round(60 / trips)} min){note}"
            for k in range(1, 25):      # no bus scheduled around now: wait for the next service
                hh = (hour + k) % 24
                if hw.get(hh, 0) > 0:
                    base = (k - 0.5) * 3600.0
                    return base + np.linspace(0, 3600.0 / hw[hh], 24), f"no bus scheduled around this hour; next service about {k} h later{note}"
        return np.linspace(0, ASSUMED_HEADWAY_S, 12), "assumed 15 min gap (little data)"

    # ---------- one trip estimate ----------
    def estimate(self, r, d, i, j, when_ist, keep=False, live=None):
        """when_ist: aware datetime = moment the rider reaches the boarding stop."""
        hour = when_ist.hour
        dtype = daytype_of_date(when_ist.strftime("%Y-%m-%d"))
        ride, lvl, basis = self.ride_samples(r, d, i, j, hour, dtype)
        live_rho, live_n, live_f = None, 0, 1.0
        if live is not None:       # live + historical: scale the learned ride time by how the LAST buses are running today
            res = self._live_rho(r, d, when_ist.timestamp(), live[0], hour, dtype)
            if res is not None:
                live_rho, live_n = res
                live_f = float(np.clip(1 + live[1] * (live_rho - 1), 0.7, 1.6))
                ride = ride * live_f
                basis = f"{basis} + live check on the last {live_n} buses"
        since = None
        if live is not None and len(live) > 2 and live[2]:     # live: how long ago did the last bus pass MY stop?
            since = self._since_last(live[0], i, when_ist.timestamp())
        wait, wbasis = self.wait_samples(r, d, i, hour, dtype, since_last=since)
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
        used_live_wait = wbasis.startswith("observed gaps, given")
        buf = (self.buffer_live if used_live_wait else self.buffer).get(conf, 0.0)
        raw_plan = float(np.percentile(tot, 90)) / 60
        out = dict(
            ride_typical_min=round(p50 / 60, 1), ride_plan_min=round(p90 / 60, 1),
            wait_typical_min=round(float(np.median(wait)) / 60, 1),
            wait_plan_min=round(float(np.percentile(wait, 90)) / 60, 1),
            total_typical_min=round(float(np.median(tot)) / 60, 1),
            total_plan_min=round(raw_plan + buf, 1), total_plan_raw_min=round(raw_plan, 1),
            confidence=conf, n_observations=int(n), basis=basis, wait_basis=wbasis, level=lvl,
            quiet_ride_min=quiet,
            on_time_rate=self.calib.get(conf),
            live_rho=None if live_rho is None else round(live_rho, 3), live_factor=round(live_f, 3), live_n=live_n,
            last_bus_ago_min=None if (since is None or not used_live_wait) else round(since / 60, 1))
        return (out, tot) if keep else out

    def _live_rho(self, r, d, when_ts, live_T, hour, dtype):
        """Ratio of (how long the most recent buses really took on the middle of the route) to (what history says).
        Uses only buses that had already finished that stretch by `when_ts` and within the last 45 minutes."""
        if live_T is None or live_T.shape[0] < 2:
            return None
        n = live_T.shape[1]
        a, b = int(n * 0.1), int(n * 0.9)
        if b - a < 4:
            return None
        ta, tb = live_T[:, a], live_T[:, b]
        ok = np.isfinite(ta) & np.isfinite(tb) & (tb <= when_ts) & (tb >= when_ts - 2700)
        if ok.sum() < 2:
            return None
        hist, _, _ = self.ride_samples(r, d, a, b, hour, dtype)
        exp = float(np.median(hist))
        if exp <= 0:
            return None
        return float(np.median(tb[ok] - ta[ok])) / exp, int(ok.sum())

    @staticmethod
    def _since_last(live_T, i, when_ts):
        if live_T is None or live_T.shape[0] == 0 or i >= live_T.shape[1]:
            return None
        col = live_T[:, i]
        col = col[np.isfinite(col) & (col <= when_ts)]
        if len(col) == 0:
            return None
        e = when_ts - float(col.max())
        return e if e <= 3 * 3600 else None

    def _live(self, rd, live_date):
        """Replay mode: the buses of `live_date` stand in for the live feed (only used for routes with GPS history)."""
        if not live_date or rd not in self.groups:
            return None
        g = self.groups[rd]
        sel = g["dates"] == live_date
        return (g["Tw"][sel], self.live_w, True) if sel.sum() >= 3 else None

    def _quiet(self, r, d, i, j, dtype):
        g = self.groups.get((r, d))
        if g is None:
            tab = self.pulse.get(dtype) or next(iter(self.pulse.values()), None)
            st = self._rd.get((r, d))
            if tab and st is not None and st.sched_sec.notna().all():
                return round(float(st.sched_sec.values[j] - st.sched_sec.values[i]) * min(x[9] for x in tab) / 60, 1)
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
        dy = (s.lat.values - lat) * 111320.0
        dx = (s.lng.values - lng) * 111320.0 * np.cos(np.radians(lat))
        out = s.assign(dist=np.hypot(dx, dy))
        return out[out.dist <= max_m].sort_values("dist")

    def _cheap_ride(self, rd, i, j):
        g = self._rd[rd]
        sc = g.sched_sec.values
        if np.isfinite(sc[i]) and np.isfinite(sc[j]) and sc[j] > sc[i]:
            return float(sc[j] - sc[i])
        return float(g.shape_dist.values[j] - g.shape_dist.values[i]) / FALLBACK_SPEED_MS

    def _leg(self, rd, i, j, e):
        g = self._rd[rd]
        return dict(route=str(g.short_name.iloc[0]), route_id=rd[0], direction=rd[1],
                    board_stop=g.stop_name.values[i], board_stop_id=g.stop_id.values[i], board_idx=int(i),
                    board_lat=float(g.lat.values[i]), board_lng=float(g.lng.values[i]),
                    alight_stop=g.stop_name.values[j], alight_stop_id=g.stop_id.values[j], alight_idx=int(j),
                    alight_lat=float(g.lat.values[j]), alight_lng=float(g.lng.values[j]),
                    ride_typical_min=e["ride_typical_min"], ride_plan_min=e["ride_plan_min"],
                    wait_typical_min=e["wait_typical_min"], wait_plan_min=e["wait_plan_min"],
                    confidence=e["confidence"], basis=e["basis"], n_observations=e["n_observations"],
                    measured=rd in self.modelled, live_n=e.get("live_n", 0), live_factor=e.get("live_factor", 1.0),
                    last_bus_ago_min=e.get("last_bus_ago_min"))

    def journeys(self, o, d, when_ist, mode="depart", max_walk=800, top=3, live_date=None):
        """o,d = (lat,lng). mode 'depart': when_ist is leaving time. 'arrive': when_ist is arrive-by.
        Returns direct options and, when useful, options with ONE change of bus."""
        so, sd = self.nearby_stops(*o, max_walk), self.nearby_stops(*d, max_walk)
        if so.empty or sd.empty:
            return []
        walk_o = {r.stop_id: r.dist * WALK_DETOUR / WALK_SPEED_MS for r in so.itertuples()}
        walk_d = {r.stop_id: r.dist * WALK_DETOUR / WALK_SPEED_MS for r in sd.itertuples()}

        # ---- direct candidates: best (boarding, alighting) pair per route-direction, ranked cheaply
        dest_by_rd = {}
        for b, wd in walk_d.items():
            for rd, j in self._stop_idx.get(b, []):
                dest_by_rd.setdefault(rd, []).append((j, b, wd))
        cand = {}
        for a, wo in walk_o.items():
            for rd, i in self._stop_idx.get(a, []):
                for j, b, wd in dest_by_rd.get(rd, []):
                    if j > i:
                        c = wo + wd + self._cheap_ride(rd, i, j)
                        if rd not in cand or c < cand[rd][0]:
                            cand[rd] = (c, i, j, a, b)
        ranked = sorted(cand.items(), key=lambda kv: kv[1][0])[:14]

        def eval_direct(rd, i, j, a, b, start):
            at_stop = start + dt.timedelta(seconds=walk_o[a])
            return self.estimate(rd[0], rd[1], i, j, at_stop, live=self._live(rd, live_date))

        results = []
        for rd, (c, i, j, a, b) in ranked:
            if mode == "depart":
                e = eval_direct(rd, i, j, a, b, when_ist)
            else:
                e = eval_direct(rd, i, j, a, b, when_ist - dt.timedelta(minutes=45))
                start = when_ist - dt.timedelta(minutes=e["total_plan_min"]) - dt.timedelta(seconds=walk_o[a] + walk_d[b])
                e = eval_direct(rd, i, j, a, b, start)
            wo, wd = walk_o[a] / 60, walk_d[b] / 60
            leg = self._leg(rd, i, j, e)
            res = dict(leg)
            res.update(e)
            res.update(walk_to_stop_min=round(wo, 1), walk_from_stop_min=round(wd, 1), transfers=0, legs=[leg])
            res["route"] = leg["route"]
            res["door_to_door_typical_min"] = round(wo + wd + e["total_typical_min"], 1)
            res["door_to_door_plan_min"] = round(wo + wd + e["total_plan_min"], 1)
            results.append(res)
        results.sort(key=lambda x: x["door_to_door_plan_min"])
        direct, seen = [], set()
        for x in results:
            k = (x["route_id"], x["direction"])
            if k not in seen:
                seen.add(k)
                direct.append(x)

        # ---- one change of bus: only when there is no direct bus, or it would save a lot
        trans = []
        if not direct or len(direct) < 2 or direct[0]["door_to_door_plan_min"] > 25:
            trans = self._transfer_options(walk_o, walk_d, when_ist, mode, live_date=live_date)
            if direct:
                best = direct[0]["door_to_door_plan_min"]
                trans = [t for t in trans if t["door_to_door_plan_min"] < best - 8]
        for x in direct + trans:
            self._times(x, when_ist, mode)
        out = sorted(direct[:top] + trans[:1], key=lambda x: x["door_to_door_plan_min"])
        return out[:top + 1]

    def _times(self, res, when_ist, mode):
        if mode == "depart":
            res["arrive_typical"] = (when_ist + dt.timedelta(minutes=res["door_to_door_typical_min"])).strftime("%H:%M")
            res["arrive_plan_for"] = (when_ist + dt.timedelta(minutes=res["door_to_door_plan_min"])).strftime("%H:%M")
        else:
            res["leave_by"] = (when_ist - dt.timedelta(minutes=res["door_to_door_plan_min"])).strftime("%H:%M")
            res["leave_typical"] = (when_ist - dt.timedelta(minutes=res["door_to_door_typical_min"])).strftime("%H:%M")

    CELL = 0.003   # about 330 m

    def _cell(self, lat, lng):
        return int(lat / self.CELL), int(lng / self.CELL)

    def _transfer_options(self, walk_o, walk_d, when_ist, mode, max_transfer_walk=300, keep_n=6, live_date=None):
        A, B = {}, {}
        for a, wo in walk_o.items():
            for rd, i in self._stop_idx.get(a, []):
                if rd not in A or wo < A[rd][2]:
                    A[rd] = (i, a, wo)
        for b, wd in walk_d.items():
            for rd, j in self._stop_idx.get(b, []):
                if rd not in B or wd < B[rd][2]:
                    B[rd] = (j, b, wd)
        grid = {}
        for rd2, (j, b, wd) in B.items():
            g = self._rd[rd2]
            for m in range(0, j):
                grid.setdefault(self._cell(g.lat.values[m], g.lng.values[m]), []).append((rd2, m))
        best = {}
        for rd1, (i, a, wo) in A.items():
            g1 = self._rd[rd1]
            la, ln = g1.lat.values, g1.lng.values
            for k in range(i + 1, len(g1)):
                ci, cj = self._cell(la[k], ln[k])
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        for rd2, m in grid.get((ci + di, cj + dj), ()):
                            if rd2[0] == rd1[0]:
                                continue
                            g2 = self._rd[rd2]
                            tw = haversine_m(la[k], ln[k], g2.lat.values[m], g2.lng.values[m])
                            if tw > max_transfer_walk:
                                continue
                            j, b, wd = B[rd2]
                            c = (wo + self._cheap_ride(rd1, i, k) + tw * WALK_DETOUR / WALK_SPEED_MS + 420
                                 + self._cheap_ride(rd2, m, j) + wd)
                            key = (rd1, rd2)
                            if key not in best or c < best[key][0]:
                                best[key] = (c, i, k, a, m, j, b, tw)
        top = sorted(best.items(), key=lambda kv: kv[1][0])[:keep_n]
        out = []
        for (rd1, rd2), (c, i, k, a, m, j, b, tw) in top:
            wo, wd = walk_o[a], walk_d[b]
            tws = tw * WALK_DETOUR / WALK_SPEED_MS
            if mode == "depart":
                start = when_ist
            else:
                start = when_ist - dt.timedelta(minutes=90)
            for _ in range(1 if mode == "depart" else 2):
                at1 = start + dt.timedelta(seconds=wo)
                e1, t1 = self.estimate(rd1[0], rd1[1], i, k, at1, keep=True, live=self._live(rd1, live_date))
                mid = at1 + dt.timedelta(minutes=e1["total_typical_min"], seconds=tws)
                e2, t2 = self.estimate(rd2[0], rd2[1], m, j, mid, keep=True, live=self._live(rd2, live_date))
                tot = t1 + tws + t2
                conf = min((e1["confidence"], e2["confidence"]), key=["low", "medium", "high"].index)
                plan = float(np.percentile(tot, 90)) / 60 + self.buffer.get(conf, 0.0)
                typ = float(np.median(tot)) / 60
                if mode == "arrive":
                    start = when_ist - dt.timedelta(minutes=plan) - dt.timedelta(seconds=wo + wd)
            l1, l2 = self._leg(rd1, i, k, e1), self._leg(rd2, m, j, e2)
            both = l1["measured"] and l2["measured"]
            res = dict(
                route=f"{l1['route']} → {l2['route']}", route_id=rd1[0], direction=rd1[1],
                board_stop=l1["board_stop"], board_stop_id=l1["board_stop_id"], board_idx=None, alight_idx=None,
                board_lat=l1["board_lat"], board_lng=l1["board_lng"],
                alight_stop=l2["alight_stop"], alight_stop_id=l2["alight_stop_id"],
                alight_lat=l2["alight_lat"], alight_lng=l2["alight_lng"],
                walk_to_stop_min=round(wo / 60, 1), walk_from_stop_min=round(wd / 60, 1),
                transfer_walk_min=round(tws / 60, 1), transfers=1, legs=[l1, l2],
                ride_typical_min=round(e1["ride_typical_min"] + e2["ride_typical_min"], 1),
                ride_plan_min=round(e1["ride_plan_min"] + e2["ride_plan_min"], 1),
                wait_typical_min=round(e1["wait_typical_min"] + e2["wait_typical_min"], 1),
                wait_plan_min=round(e1["wait_plan_min"] + e2["wait_plan_min"], 1),
                total_typical_min=round(typ, 1), total_plan_min=round(plan, 1),
                total_plan_raw_min=round(plan - self.buffer.get(conf, 0.0), 1),
                confidence=conf, n_observations=min(e1["n_observations"], e2["n_observations"]),
                basis=("GPS history on both buses" if both else "partly estimated from timetable x city pulse"),
                wait_basis=e1["wait_basis"], level=max(e1["level"], e2["level"]),
                quiet_ride_min=None, on_time_rate=self.calib.get(conf))
            res["door_to_door_typical_min"] = round(wo / 60 + wd / 60 + typ, 1)
            res["door_to_door_plan_min"] = round(wo / 60 + wd / 60 + plan, 1)
            out.append(res)
        out.sort(key=lambda x: x["door_to_door_plan_min"])
        return out

    def _all_groups(self, modelled_only=False):
        for (r, d), g in self._rd.items():
            if modelled_only and (r, d) not in self.modelled:
                continue
            yield (r, d), g

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

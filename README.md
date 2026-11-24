# LeaveWise — reliability-aware "leave-by" bus planner for Delhi (Transport Stack Hackathon 2026, Problem 1)

Instead of one number ("35 min"), LeaveWise tells a commuter **when to leave** to reach a place by a given time,
including the **wait for the bus**, and says **how sure it is** (traffic-light + "worked about 87 times in 100 on a day it had not seen").
All numbers come from what DTC buses actually did in the October 2026 GPS data.

## What is in this repo
| Step | Command | What it does |
|---|---|---|
| 0 | `python -m leavewise.inspect_day --db DAY.db --gtfs GTFS_DIR` | shows the real data format, busiest routes, and whether route ids match GTFS |
| 1 | `python -m leavewise.gtfs_prep --gtfs GTFS_DIR --routes 534,764,... --out data` | shrinks static GTFS to the chosen routes (stops, order, shape) |
| 2 | `python -m leavewise.build_day --db DAY.db --routes 534,764,... --data data --out passes` | one day of GPS → time each bus reached each stop (run for each day) |
| 3 | `python -m leavewise.validate --data data --passes passes` | hold-out-day validation → `validation_report.md`, `calibration.json`, `gmaps_template.csv` |
| 4 | `python -m leavewise.app --data data --passes passes` | API + screen at http://127.0.0.1:8000 |
| – | `python -m pytest -q tests` | tests on synthetic data |
| – | `python -m leavewise.gen_synthetic --out syn` | FAKE data in the real format, for testing only |

Install: `pip install pandas numpy flask` (Python 3.9+).

## API
`GET /api/journeys?from=LAT,LNG&to=LAT,LNG&date=2026-10-07&depart=08:30` → options with walk, boarding stop, route, alight stop, typical and plan-for time, arrival.
`GET /api/journeys?...&arrive_by=09:30` → same plus `leave_by` (the key feature). `from`/`to` also accept `stop:<stop_id>`.
Each option includes `confidence` (high/medium/low), `n_observations`, `basis` (which fallback level was used), `quiet_ride_min` (usual time without the rush) and `on_time_rate` (from validation).
Also: `/api/stops`, `/api/health`, `/api/card` (hour-by-hour table for a printable stop card).

## Method (short)
1. **GPS → route.** Each bus run is projected onto the GTFS route line (numpy, no map-matching library). Direction is chosen by best fit; pings >80 m off the line, duplicates, >108 km/h jumps and backward noise are dropped.
2. **Stop pass times.** Time is interpolated at each stop's position along the line. If the nearest pings are >4 min or >1.5 km apart, that stop pass is **discarded, not guessed**.
3. **Reliability model.** For a stop pair, hour and day type, we keep the real distribution of ride times (median, P90) and of waits (from observed gaps between buses, weighted because riders arrive randomly).
4. **Leave-by.** arrive-by − walk − P90 of (wait + ride, simulated together) − walk. Waiting is part of the plan, treated separately from riding.
5. **Fallback ladder when data is thin:** same hour & day type → same day type all hours → all days → timetable → distance at 14 km/h. The level used is shown to the user as `basis`, and confidence drops.
6. **Validation:** model never sees the hold-out day; compared with timetable and all-day-average baselines; plan-for coverage and confidence calibration reported. Google Maps comparison: fill `gmaps_minutes` in `gmaps_template.csv` by hand, then `python -m leavewise.validate --gmaps gmaps_template.csv`.

## Known limits (honest)
- Direct single-route trips only; no transfers, crowding or live-feed blending yet (live GTFS-RT would shift the typical time for the next bus; planned for Phase 2).
- Route line with loops/short-turn variants uses one shape per direction.
- Holiday list (`common.HOLIDAYS`) must be checked; DTC buses only (no cluster buses in the history).
- Unit tests use synthetic data; all reported results use the real October 2026 GPS data (see validation_report.md and TECHNICAL_NOTE.md).

## Credits
pandas, numpy, Flask. Data: Delhi Transport Stack (JICA / Chartr / IIIT Delhi).

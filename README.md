# Pahunch (पहुँच) / LeaveWise — reach on time, and know how sure we are

**Transport Stack Hackathon 2026 · Phase 1 · Problem 1: real-time bus traffic model + journey planner API**

Most apps give one number ("35 min"). Pahunch answers the question a commuter really has:
**"When must I leave to reach by 9:30?"** It includes the **wait for the bus**, and says **how sure it is**,
using a confidence level that was **tested on a day the model never saw**.

## Results on real Delhi data (5 different test days, each never seen by the model)
| Test day | Test cases | Ride-time error (min) | "Plan for" was enough |
|---|---|---|---|
| 8 Oct | 4,182 | 3.58 | 85% |
| 9 Oct | 3,951 | 3.10 | 90% |
| 12 Oct | 4,009 | 3.95 | 83% |
| 13 Oct | 4,865 | 2.97 | 89% |
| 14 Oct | 4,717 | 3.28 | 87% |
| **Average** | | **3.38** | **87%** (range 83–90%) |

- Ride-time error was lower than the published timetable (5.4–6.1 min on four of the five days) and than the all-day average (4.5–5.6 min) on every test day.
- Each test day was held out completely. The safety buffer was learned on a *different* earlier weekday.
- Target was 90% on time; we reach 83–90%, average 87%. Midday (13:00) is the weakest hour (74–87%). Full reports: `multi/` and `validation_report.md`.

## What is different
1. **Leave-by, not just duration.** Wait + ride are simulated together; the plan uses the P90.
2. **Honest confidence.** 🙂 / 😐 / 😟 comes from data volume and spread, and each level has a measured on-time rate.
3. **One reliability table, many outputs.** The same table feeds the API, the screen, a printable stop card, a 160-character SMS text, and an operator view of unreliable route-hours.
4. **For everyone.** Icons, Hindi text and voice, WhatsApp share, printable card, works offline for answers already viewed.
5. **Sells nothing.** No tickets, no payments, no login. Nothing to refund and no personal data.

## Data and method
- Real DTC GPS (October 2026) + real static GTFS (2,574 routes; 6 routes modelled). GPS route ids match GTFS route ids.
- Each bus run is projected on the GTFS route line (numpy only), direction chosen by best fit; pings >80 m off the line, duplicates, >108 km/h jumps and backward noise dropped.
- Stop pass times are interpolated; passes across GPS gaps >240 s or >1.5 km are **discarded, not guessed**.
- Statistics (median, P90) per route-direction, stop pair, hour (±1 h) and day type (weekday/weekend/holiday).
- Wait times come from observed gaps between buses, weighted because riders arrive at random moments.
- Fallback when data is thin: same hour+day type → same day type → all days → timetable → distance at 14 km/h. The level used is shown as `basis` and lowers confidence.

## Run it
Python 3.9+.
```
pip install -r requirements.txt
python -m leavewise.app --data data --passes passes
```
Open http://127.0.0.1:5000 (use the port printed in the terminal). Tests: `python -m pytest -q tests`.

### API
| Endpoint | Purpose |
|---|---|
| `GET /api/journeys?from=LAT,LNG&to=LAT,LNG&date=2026-10-07&arrive_by=09:30` | options with `leave_by`, walk, boarding stop, route, alight stop, typical and plan-for minutes, `confidence`, `n_observations`, `basis`, `on_time_rate` |
| same with `&depart=08:30` | arrival-time options |
| `/api/sms?...` | ≤160-character text answer (`lang=hi` for Hindi) |
| `/api/card` | hour-by-hour table for the printable stop card |
| `/api/operator` | route × hour reliability table for operators |
| `/api/stops`, `/api/routes`, `/api/health` | support endpoints |

`from` / `to` also accept `stop:<stop_id>`.

### Rebuild from raw data (optional)
```
python -m leavewise.gtfs_prep --gtfs GTFS_DIR --routes 729,764,78,... --out data
python -m leavewise.build_day --db DAY.db --routes 729,764,78,... --data data --out passes
python -m leavewise.validate --data data --passes passes
```
Raw GPS `.db` files (16 GB) are not in this repo; `data/` and `passes/` hold the processed results.

## Known limits (honest)
- Only 6 routes, direct trips only: no transfers yet.
- Tested on 5 October weekdays only; midday reliability is weaker (74–87%).
- Historical statistics only. Waiting uncertainty is the biggest error source; blending the live GTFS-RT feed for the next bus is the Phase 2 step.
- Google Maps cannot show past dates, so a Google comparison is only a sanity check, not proof.
- Offline: the screen, stop list and previously viewed answers work without internet; new questions and map tiles need a connection.

## Credits
pandas, numpy, Flask, Leaflet + OpenStreetMap. Data: Delhi Transport Stack (JICA / Chartr / IIIT Delhi).

# Pahunch transfer validation

Test day (never seen by the model): **2026-10-14** (weekday). 174 simulated trips with one change of bus, 29 route pairs, hours [8, 13, 18, 22].
Each trip: next real bus on route 1, then the next real bus on route 2 after walking between the stops.

## Was the plan-for time enough? (target 90%)

Before the buffer: **77%**. After the buffer (what the app uses): **81%**. Typical time alone: 36%.
Typical-time error: 33.3 min on average (trips take 90 min on average).
Cost of caution: you arrive **40 min** earlier than needed on average (median 37 min).

| Confidence | Trips | Plan-for was enough |
|---|---|---|
| high | 56 | 75% |
| medium | 96 | 91% |
| low | 22 | 55% |

| Hour | Trips | Plan-for was enough |
|---|---|---|
| 8:00 | 44 | 91% |
| 13:00 | 56 | 75% |
| 18:00 | 67 | 78% |
| 22:00 | 7 | 100% |

## Limits

- Only pairs of routes that BOTH have GPS history are tested. Changes involving timetable-only routes are marked 'estimated' in the app and are not part of this test.
- Walking between stops uses the planner's own walking estimate (no GPS on foot).
- One test day. Re-run with `--holdout` for other days and report the range.

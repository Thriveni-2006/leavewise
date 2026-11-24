# LeaveWise validation report

Test day (never seen by the model): **2026-10-14** (weekday). The extra safety buffer was learned on **2026-10-13**, a different day. Model trained on 9 days: 2026-10-05, 2026-10-06, 2026-10-07, 2026-10-08, 2026-10-09, 2026-10-10, 2026-10-11, 2026-10-12, 2026-10-13.
Test cases: 4717 rider arrivals (random moments inside hours [8, 13, 18, 22]) over 6 routes.

## 1. How good is the typical in-vehicle time?

| Method | MAE (min) | MAPE (%) |
|---|---|---|
| LeaveWise (hour + day-type median) | 3.28 | 19.9 |
| All-day average (no time-of-day) | 4.33 | 27.7 |
| Published timetable | 4.74 | 31.8 |

## 2. Is the plan-for time really enough? (wait + ride; target 90%)

Before the buffer: **83%**. After adding the buffer (what the app uses): **87%**. Typical time alone: 45%.
Ride part alone, P90 covered: 86%. Wait part alone, P90 covered: 83%.

Extra buffer learned (minutes, capped at 20): high +0.0, medium +20.0, low +20.0

Cost of caution: on average you arrive **36 min** earlier than needed (median 28 min).

| Confidence label | Cases | Plan-for was enough | Ride typical error (min) |
|---|---|---|---|
| high | 2106 | 88% | 2.2 |
| medium | 2383 | 85% | 3.7 |
| low | 228 | 97% | 8.4 |

## 3. By time of day

| Hour | Cases | Ride typical error (min) | Plan-for enough |
|---|---|---|---|
| 8:00 | 1333 | 2.3 | 97% |
| 13:00 | 1297 | 2.8 | 74% |
| 18:00 | 1320 | 4.9 | 86% |
| 22:00 | 767 | 3.0 | 94% |

## 4. Where it is weakest

- Cases that fell to a fallback level (thin data): 2%.
- One test day is a small test. Re-run with other days (`--holdout`) and report the range.
- Wait time depends on how many bus passes were seen; gaps in GPS make some waits look longer.
- Journeys with transfers, signal-level delay, and live updates are not validated here.
# Pahunch validation report

Test day (never seen by the model): **2026-10-14** (weekday). The extra safety buffer was learned on **2026-10-13**, a different day. Model trained on 9 days: 2026-10-05, 2026-10-06, 2026-10-07, 2026-10-08, 2026-10-09, 2026-10-10, 2026-10-11, 2026-10-12, 2026-10-13.
Test cases: 15770 rider arrivals (random moments inside hours [8, 13, 18, 22]) over 25 routes.

## 1. How good is the typical in-vehicle time?

| Method | MAE (min) | MAPE (%) |
|---|---|---|
| Pahunch (hour + day-type median) | 3.25 | 17.9 |
| All-day average (no time-of-day) | 4.54 | 27.4 |
| Published timetable | 5.51 | 30.3 |

## 2. Is the plan-for time really enough? (wait + ride; target 90%)

Before the buffer: **86%**. After adding the buffer (what the app uses): **89%**. Typical time alone: 48%.
Ride part alone, P90 covered: 87%. Wait part alone, P90 covered: 87%.

Extra buffer learned (minutes, capped at 20): high +0.0, medium +20.0, low +5.6

Cost of caution: on average you arrive **33 min** earlier than needed (median 24 min).

| Confidence label | Cases | Plan-for was enough | Ride typical error (min) |
|---|---|---|---|
| high | 9230 | 88% | 2.6 |
| medium | 5673 | 91% | 3.9 |
| low | 867 | 84% | 5.8 |

## 3. By time of day

| Hour | Cases | Ride typical error (min) | Plan-for enough |
|---|---|---|---|
| 8:00 | 4491 | 1.9 | 95% |
| 13:00 | 4330 | 2.9 | 84% |
| 18:00 | 4487 | 5.4 | 86% |
| 22:00 | 2462 | 2.3 | 89% |

## 5. Where it is weakest

- Cases that fell to a fallback level (thin data): 1%.
- One test day is a small test. Re-run with other days (`--holdout`) and report the range.
- Wait time depends on how many bus passes were seen; gaps in GPS make some waits look longer.
- Journeys with transfers and signal-level delay are not validated here. The live check is a replay of a past day (the real feed was not available to us).
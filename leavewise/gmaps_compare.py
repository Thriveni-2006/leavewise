"""Compare Pahunch with Google Maps for the same origin, destination and time.

  python -m leavewise.gmaps_compare --template            -> writes gmaps_pairs.csv (stop names that exist in our data)
  (you fill gmaps_minutes from Google Maps: Directions > Transit > Bus only, same date and time)
  python -m leavewise.gmaps_compare --filled gmaps_pairs.csv --out gmaps_report.md
"""
import argparse
import datetime as dt
import numpy as np
import pandas as pd
from .common import IST
from .model import Model
from .app import find_stop

LANDMARKS = [("Kashmere Gate", "Nehru Place"), ("Connaught Place", "Saket"), ("Anand Vihar", "Connaught Place"),
             ("Rohini", "Connaught Place"), ("Lajpat Nagar", "Karol Bagh"), ("AIIMS", "Red Fort"),
             ("Hauz Khas", "Anand Vihar"), ("Nehru Place", "Connaught Place"), ("Kashmere Gate", "Lajpat Nagar"),
             ("Dwarka", "Connaught Place"), ("Saket", "Kashmere Gate"), ("Karol Bagh", "Nehru Place")]


def maps_url(r):
    import urllib.parse
    q = urllib.parse.quote
    if "from_lat" in r.index and str(r.from_lat).strip():
        o, d = f"{r.from_lat},{r.from_lng}", f"{r.to_lat},{r.to_lng}"
    else:
        o, d = r.google_search_from + ", Delhi", r.google_search_to + ", Delhi"
    return "https://www.google.com/maps/dir/?api=1&travelmode=transit&origin=" + q(o) + "&destination=" + q(d)


def template(m, out, date):
    rows = []
    for a, b in LANDMARKS:
        sa, sb = find_stop(a, m), find_stop(b, m)
        if sa is None or sb is None:
            print(f"skipped {a} -> {b}: not found in our stops")
            continue
        rows.append(dict(from_name=sa.stop_name, to_name=sb.stop_name, google_search_from=a, google_search_to=b,
                         from_lat=round(float(sa.lat), 6), from_lng=round(float(sa.lng), 6),
                         to_lat=round(float(sb.lat), 6), to_lng=round(float(sb.lng), 6),
                         date=date, depart="08:30", gmaps_minutes=""))
    rows.append(dict(rows[0], depart="23:00"))
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"Wrote {out} with {len(rows)} rows. Google Maps opens with exact coordinates; set Transit > Bus only, "
          f"date {date}, leave at the time shown; type the minutes (door to door) into gmaps_minutes.")


def compare(m, filled, out_md):
    g = pd.read_csv(filled, dtype=str).fillna("")
    g = g[g.gmaps_minutes.str.strip() != ""]
    out = []
    for r in g.itertuples():
        if getattr(r, "from_lat", "") and str(r.from_lat).strip():
            class _P: pass
            sa, sb = _P(), _P()
            sa.lat, sa.lng, sb.lat, sb.lng = float(r.from_lat), float(r.from_lng), float(r.to_lat), float(r.to_lng)
        else:
            sa, sb = find_stop(r.from_name, m), find_stop(r.to_name, m)
        if sa is None or sb is None:
            continue
        h, mi = r.depart.split(":")
        when = dt.datetime.fromisoformat(r.date).replace(tzinfo=IST, hour=int(h), minute=int(mi))
        js = m.journeys((sa.lat, sa.lng), (sb.lat, sb.lng), when, "depart")
        if not js:
            out.append(dict(origin=r.google_search_from, dest=r.google_search_to, depart=r.depart, google=float(r.gmaps_minutes),
                            ours_typical=np.nan, ours_plan=np.nan, route="no option", transfers=np.nan))
            continue
        x = js[0]      # the option the screen shows first (lowest plan-for time)
        out.append(dict(origin=r.google_search_from, dest=r.google_search_to, depart=r.depart, google=float(r.gmaps_minutes),
                        ours_typical=x["door_to_door_typical_min"], ours_plan=x["door_to_door_plan_min"],
                        route=x["route"], transfers=x["transfers"]))
    d = pd.DataFrame(out)
    print(d.to_string(index=False))
    ok = d.dropna(subset=["ours_typical"])
    lines = ["# Pahunch vs Google Maps", "", f"Pairs compared: {len(ok)} of {len(d)} (rest: no bus option found)", ""]
    if len(ok):
        err = (ok.ours_typical - ok.google).abs()
        inrange = ((ok.google >= ok.ours_typical * 0.85) & (ok.google <= ok.ours_plan)).mean() * 100
        lines += [f"- Mean difference (typical vs Google): **{err.mean():.1f} min**; median {err.median():.1f} min",
                  f"- Median difference as a share of Google's time: **{(err / ok.google).median() * 100:.0f}%**",
                  f"- Google's time fell inside our range (typical -15% to plan-for): **{inrange:.0f}%** of pairs", "",
                  "```", d.to_string(index=False), "```", "",
                  "Notes: Google was queried for a future departure; Google counts door-to-door including walking and its own wait. "
                  "Pahunch's 'typical' is the median of observed bus behaviour, 'plan-for' is the 90th percentile. "
                  "This is a sanity check, not proof; the held-out-day validation is the main test."]
    open(out_md, "w", encoding="utf-8").write("\n".join(lines))
    print(f"\nWrote {out_md}")
    if len(ok):
        print(f"Mean difference vs Google: {err.mean():.1f} min; Google inside our range for {inrange:.0f}% of pairs")


def fill(path, open_browser=True):
    """Walk through the rows: opens Google Maps for each trip, asks for the minutes, saves after every row."""
    import urllib.parse
    import webbrowser
    g = pd.read_csv(path, dtype=str).fillna("")
    first = True
    for k in range(len(g)):
        if g.loc[k, "gmaps_minutes"].strip():
            continue
        r = g.loc[k]
        url = maps_url(r)
        print(f"\n--- Trip {k + 1} of {len(g)}: {r.google_search_from}  ->  {r.google_search_to}")
        print(f"    In Google Maps set: Depart at {r.depart} on {r.date}   (Options: Bus only)")
        if open_browser:
            webbrowser.open(url)
        if first and open_browser:
            print("    (first time only) Click Options and tick 'Bus only', untick Subway/Train.")
            first = False
        while True:
            a = input("    Minutes shown for the best bus route (number only; Enter = skip; q = save and stop): ").strip().lower()
            if a == "q":
                g.to_csv(path, index=False); print("Saved."); return
            if a == "":
                break
            try:
                v = float(a)
                if 5 <= v <= 300:
                    g.loc[k, "gmaps_minutes"] = str(int(v) if v == int(v) else v)
                    break
                print("    That looks wrong (expected 5 to 300 minutes). Type the total minutes, e.g. 72 for 1 hr 12 min.")
            except ValueError:
                print("    Type only a number, e.g. 72 for 1 hr 12 min.")
        g.to_csv(path, index=False)
    print("\nAll rows done and saved.")


def open_all(path):
    """Opens every trip in its own browser tab (so you can set Bus only + time in each, then type the numbers)."""
    import urllib.parse
    import webbrowser
    g = pd.read_csv(path, dtype=str).fillna("")
    for k in range(len(g)):
        r = g.loc[k]
        url = maps_url(r)
        webbrowser.open_new_tab(url)
        print(f"Tab {k + 1}: {r.google_search_from} -> {r.google_search_to}   set Depart at {r.depart} on {r.date}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="data"); ap.add_argument("--passes", default="passes")
    ap.add_argument("--template", action="store_true"); ap.add_argument("--filled", default=None)
    ap.add_argument("--fill", default=None, help="interactive: opens Google Maps for each row and asks for the minutes")
    ap.add_argument("--openall", default=None, help="open every trip in its own browser tab")
    ap.add_argument("--noopen", action="store_true", help="with --fill: do not open Google Maps, just ask for the numbers")
    ap.add_argument("--date", default="2026-11-27"); ap.add_argument("--out", default="gmaps_report.md")
    a = ap.parse_args()
    if a.openall:
        return open_all(a.openall)
    if a.fill:
        return fill(a.fill, open_browser=not a.noopen)
    m = Model(a.data, a.passes)
    if a.template:
        template(m, "gmaps_pairs.csv", a.date)
    elif a.filled:
        compare(m, a.filled, a.out)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()

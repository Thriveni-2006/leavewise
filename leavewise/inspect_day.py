"""Step 0: look at ONE real GPS day before anything else. Prints what you must send back to me.
Usage: python -m leavewise.inspect_day --db bus_movements_2026_10_15.db [--gtfs path_to_gtfs_folder]
"""
import argparse
import os
import sqlite3
import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--gtfs", default=None)
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    print("TABLES:", [r[0] for r in con.execute("select name from sqlite_master where type='table'")])
    print("SCHEMA:", con.execute("select sql from sqlite_master where name='vehicle_feed'").fetchone()[0])
    n = con.execute("select count(*) from vehicle_feed").fetchone()[0]
    print("ROWS:", n)
    print("SAMPLE:")
    print(pd.read_sql_query("select * from vehicle_feed limit 5", con).to_string())
    print("TIME RANGE (IST):", con.execute("select min(date||' '||time), max(date||' '||time) from vehicle_feed").fetchone())
    print("BUSES:", con.execute("select count(distinct vehicle_id) from vehicle_feed").fetchone()[0],
          "| ROUTES:", con.execute("select count(distinct route_id) from vehicle_feed").fetchone()[0],
          "| TRIPS:", con.execute("select count(distinct trip_id) from vehicle_feed").fetchone()[0])
    top = pd.read_sql_query("select route_id, count(*) pings, count(distinct vehicle_id) buses, count(distinct trip_id) trips "
                            "from vehicle_feed where route_id is not null group by route_id order by pings desc limit 15", con)
    print("\nBUSIEST ROUTES (candidates to model):")
    print(top.to_string(index=False))
    print("\nNULL/blank counts:", con.execute("select sum(route_id is null), sum(trip_id is null or trip_id=''), sum(lat is null) from vehicle_feed").fetchone())
    ids = [str(int(r)) for r in top.route_id.head(15)]
    print("\nSuggested --routes:", ",".join(ids[:8]))
    if a.gtfs:
        rt = pd.read_csv(os.path.join(a.gtfs, "routes.txt"), dtype=str)
        hit = [i for i in ids if i in set(rt.route_id)]
        print(f"GTFS routes.txt has {len(rt)} routes; {len(hit)}/{len(ids)} of the busiest GPS route_ids match route_id exactly: {hit}")
        print("Example GTFS route_id values:", rt.route_id.head(8).tolist())
        tr = pd.read_csv(os.path.join(a.gtfs, "trips.txt"), dtype=str, nrows=2000)
        print("trips.txt columns:", list(tr.columns))
        gt = set(pd.read_csv(os.path.join(a.gtfs, "trips.txt"), dtype=str, usecols=["trip_id"]).trip_id)
        gps = set(pd.read_sql_query("select distinct trip_id from vehicle_feed limit 2000", con).trip_id)
        print(f"GPS trip_id found in GTFS trips.txt: {len(gps & gt)}/{len(gps)}  (LeaveWise does not need this to match)")


if __name__ == "__main__":
    main()

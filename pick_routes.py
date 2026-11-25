import sqlite3, pandas as pd
con = sqlite3.connect(r'D:\data\bus_movements_2026_10_13.db')
top = pd.read_sql("select route_id, count(*) n from vehicle_feed group by route_id order by n desc limit 26", con)
ids = [str(int(x)) for x in top.route_id]
old = [str(x) for x in pd.read_csv('data_v1/route_stops.csv').route_id.unique()]
allr = list(dict.fromkeys(old + ids))
open('routes30.txt','w').write(','.join(allr))
print(len(allr), 'routes:', ','.join(allr))

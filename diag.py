import datetime as dt
from leavewise.model import Model
from leavewise.app import find_stop
from leavewise.common import IST
m = Model('data', 'passes')
tests = [("Nehru Place","Connaught Place","08:30",95), ("Rohini","Connaught Place","08:30",100),
         ("Kashmere Gate","Nehru Place","23:00",99), ("Saket","Kashmere Gate","08:30",69)]
for a, b, t, g in tests:
    sa, sb = find_stop(a, m), find_stop(b, m)
    h, mi = t.split(':')
    when = dt.datetime(2026,11,27,int(h),int(mi),tzinfo=IST)
    print(f"\n=== {a} -> {b} at {t}   Google: {g} min   (stops: {sa.stop_name} | {sb.stop_name})")
    for x in m.journeys((sa.lat,sa.lng),(sb.lat,sb.lng),when,'depart'):
        for l in x['legs']:
            print(f"  route {l['route']:10} {l['board_stop'][:28]} -> {l['alight_stop'][:28]} | ride typ {l['ride_typical_min']} plan {l['ride_plan_min']} | wait typ {l['wait_typical_min']} plan {l['wait_plan_min']} | {l['basis'][:40]}")
        print(f"    walk {x['walk_to_stop_min']}+{x['walk_from_stop_min']}  total typical {x['door_to_door_typical_min']}  plan {x['door_to_door_plan_min']}  wait: {x['wait_basis']}")

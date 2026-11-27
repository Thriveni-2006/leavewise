import datetime as dt, os, subprocess, sys
import pytest
from leavewise.common import Polyline, IST, daytype_of_date
from leavewise import gen_synthetic as gs, build_day
from leavewise.model import Model

ROUTES = ["500", "501", "502"]

@pytest.fixture(scope="session")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("syn")
    rs, sh = gs.build_static(str(root / "data"))
    passes = root / "passes"; passes.mkdir()
    for date in ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-10"]:
        db = str(root / f"b_{date}.db")
        gs.simulate_day(date, rs, sh, db)
        out, stats = build_day.process_day(db, str(root / "data"), ROUTES)
        out.to_csv(passes / f"passes_{stats['date']}.csv", index=False)
    return str(root / "data"), str(passes)

def test_projection_roundtrip():
    lat = [28.5 + i * 0.001 for i in range(50)]; lng = [77.2] * 50
    p = Polyline(lat, lng)
    d, r = p.project([28.5 + 0.0105], [77.2001])
    assert abs(d[0] - 1160) < 40 and r[0] < 15

def test_daytype():
    assert daytype_of_date("2026-10-07") == "weekday"
    assert daytype_of_date("2026-10-10") == "weekend"
    assert daytype_of_date("2026-10-02") == "weekend"  # holiday list

def test_peak_slower_than_night(world):
    m = Model(*world)
    when = lambda h: dt.datetime(2026, 10, 7, h, 0, tzinfo=IST)
    peak = m.estimate("500", 0, 2, 12, when(8))
    quiet = m.estimate("500", 0, 2, 12, when(13))
    assert peak["ride_typical_min"] > quiet["ride_typical_min"] * 1.15
    assert peak["total_plan_min"] >= peak["total_typical_min"]

def test_journeys_and_leaveby(world):
    m = Model(*world)
    st = m.rs[(m.rs.route_id == "500") & (m.rs.direction == 0)].sort_values("seq")
    o = (st.lat.iloc[2], st.lng.iloc[2]); d = (st.lat.iloc[12], st.lng.iloc[12])
    js = m.journeys(o, d, dt.datetime(2026, 10, 7, 9, 30, tzinfo=IST), "arrive")
    assert js and js[0]["route"].startswith("500")
    assert js[0]["leave_by"] < "09:30" and js[0]["leave_by"] <= js[0]["leave_typical"]

def test_thin_data_falls_back(world):
    m = Model(*world)
    e = m.estimate("500", 0, 2, 12, dt.datetime(2026, 10, 7, 3, 0, tzinfo=IST))
    assert e["level"] >= 1 and e["confidence"] in ("high", "medium", "low")

def test_api(world):
    from leavewise.app import create_app
    c = create_app(Model(*world)).test_client()
    assert c.get("/api/health").json["ok"]
    s = c.get("/api/stops").json
    a, b = s[2], s[12]
    r = c.get(f"/api/journeys?from={a['lat']},{a['lng']}&to={b['lat']},{b['lng']}&date=2026-10-07&arrive_by=09:30")
    assert r.status_code == 200 and r.json["options"]
    assert c.get("/api/journeys").status_code == 400

def test_operator_table(world):
    m = Model(*world)
    rows = m.reliability_table("weekday")
    assert rows and all(r["index"] >= 1.0 for r in rows)
    peak = [r for r in rows if r["hour"] == 8]; quiet = [r for r in rows if r["hour"] == 13]
    assert max(r["median_min"] for r in peak) > min(r["median_min"] for r in quiet)

def test_sms_text(world):
    from leavewise.app import create_app
    c = create_app(Model(*world)).test_client()
    s = c.get("/api/stops").json
    r = c.get(f"/api/sms?from={s[2]['lat']},{s[2]['lng']}&to={s[12]['lat']},{s[12]['lng']}&date=2026-10-07&arrive_by=09:30")
    assert r.status_code == 200 and r.json["length"] <= 160 and "Leave by" in r.json["text"]


def test_network_transfer_and_names(tmp_path):
    """A journey that needs one change of bus, on a tiny made-up network (timetable only)."""
    import datetime as dt
    import pandas as pd
    from leavewise.model import Model
    from leavewise.common import IST
    rows = []
    def route(rid, name, pts):
        for k, (la, lo) in enumerate(pts):
            rows.append((rid, name, 0, k, f"{rid}_{k}", f"{name} stop {k}", la, lo, k * 1000.0, k * 240.0))
    route("R1", "10", [(28.60, 77.10 + 0.01 * k) for k in range(10)])
    route("R2", "20", [(28.60 + 0.01 * k, 77.19) for k in range(10)])
    route("R3", "30", [(28.70, 77.30 + 0.01 * k) for k in range(8)])
    df = pd.DataFrame(rows, columns=["route_id", "short_name", "direction", "seq", "stop_id", "stop_name", "lat", "lng", "shape_dist", "sched_sec"])
    d = tmp_path / "data"; d.mkdir(); (tmp_path / "passes").mkdir()
    df[df.route_id == "R3"].to_csv(d / "route_stops.csv", index=False)
    df[df.route_id != "R3"].to_csv(d / "network_stops.csv", index=False)
    m = Model(str(d), str(tmp_path / "passes"))
    when = dt.datetime(2026, 11, 25, 8, 0, tzinfo=IST)
    res = m.journeys((28.6002, 77.1002), (28.69, 77.1902), when, "depart")
    assert res and res[0]["transfers"] == 1 and len(res[0]["legs"]) == 2
    assert res[0]["confidence"] == "low"          # no GPS history on these routes -> honest low confidence
    from leavewise.app import create_app
    c = create_app(m).test_client()
    r = c.get("/api/journeys?from=10 stop 0&to=20 stop 9&date=2026-11-25&depart=08:00")
    assert r.status_code == 200 and r.get_json()["options"]


def test_offline_pack(world):
    from leavewise.export_pack import build
    pack, checks = build(Model(*world), check_n=30)
    assert pack["routes"] and len(checks) == 30
    rt = pack["routes"][0]
    h = rt["tab"]["weekday"][8]
    assert len(h["seg"]) == len(rt["stops"]) - 1 and len(h["w"]) == 20 and h["c"] in ("high", "medium", "low")


def test_voice_matching():
    import pandas as pd
    from leavewise.voice import skeleton, best_matches
    assert skeleton("नेहरू प्लेस") == skeleton("Nehru Place")
    assert skeleton("कश्मीरी गेट") == skeleton("Kashmere Gate")
    st = pd.DataFrame(dict(stop_id=["a", "b", "c"], stop_name=["Nehru Place Terminal", "Kashmere Gate ISBT", "Rohini Sector 7"], lat=[1.0, 2.0, 3.0], lng=[1.0, 2.0, 3.0]))
    cnt = {"a": 20, "b": 40, "c": 5}
    assert best_matches(st, cnt, ["नेहरू प्लेस"])[0]["id"] == "a"
    assert best_matches(st, cnt, ["cashmere gate", "kashmir gate"])[0]["id"] == "b"
    assert best_matches(st, cnt, ["रोहिणी"])[0]["id"] == "c"

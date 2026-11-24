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

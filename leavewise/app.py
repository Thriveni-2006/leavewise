"""Step 5: API + icon-first screen.   Run:  python -m leavewise.app --data data --passes passes
GET /api/journeys?from=lat,lng&to=lat,lng&date=YYYY-MM-DD&depart=HH:MM     (or arrive_by=HH:MM for leave-by)
GET /api/stops          list of stops (for the pickers)
GET /api/card?route_id=&direction=&from_stop=&to_stop=&daytype=weekday      hour-by-hour table (printable)
"""
import argparse
import datetime as dt
import os
import pandas as pd
from flask import Flask, jsonify, request, send_from_directory
from .common import IST
from .model import Model

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def parse_point(s, model):
    if s.startswith("stop:"):
        r = model._stops_all[model._stops_all.stop_id == s[5:]]
        if r.empty:
            raise ValueError("unknown stop " + s)
        return float(r.lat.iloc[0]), float(r.lng.iloc[0])
    a, b = s.split(",")
    return float(a), float(b)


def create_app(model):
    app = Flask(__name__, static_folder=STATIC, static_url_path="")

    @app.get("/")
    def home():
        return send_from_directory(STATIC, "index.html")

    @app.get("/api/health")
    def health():
        return jsonify(ok=True, days_of_data=len(model.dates), routes=sorted({k[0] for k in model.groups}))

    @app.get("/api/stops")
    def stops():
        s = model._stops_all
        return jsonify([dict(id=r.stop_id, name=r.stop_name, lat=r.lat, lng=r.lng) for r in s.itertuples()])

    @app.get("/api/journeys")
    def journeys():
        try:
            o = parse_point(request.args["from"], model)
            d = parse_point(request.args["to"], model)
            date = request.args.get("date") or dt.datetime.now(IST).strftime("%Y-%m-%d")
            if "arrive_by" in request.args:
                mode, hhmm = "arrive", request.args["arrive_by"]
            else:
                mode, hhmm = "depart", request.args.get("depart", dt.datetime.now(IST).strftime("%H:%M"))
            h, m = hhmm.split(":")
            when = dt.datetime.fromisoformat(date).replace(tzinfo=IST, hour=int(h), minute=int(m))
        except (KeyError, ValueError) as e:
            return jsonify(error=f"bad request: {e}"), 400
        res = model.journeys(o, d, when, mode)
        return jsonify(mode=mode, when=when.isoformat(), options=res,
                       note=None if res else "No direct bus found within 800 m walk of both points.")

    shapes = pd.read_csv(os.path.join(model.data_dir, "shapes.csv"), dtype={"route_id": str})

    @app.get("/api/shape")
    def shape():
        g = shapes[(shapes.route_id == request.args["route_id"]) & (shapes.direction == int(request.args.get("direction", 0)))]
        g = g.sort_values("seq")
        step = max(1, len(g) // 400)
        g = g.iloc[::step]
        return jsonify([[round(a, 5), round(b, 5)] for a, b in zip(g.lat, g.lng)])

    @app.get("/api/routes")
    def routes():
        out = []
        for (r, d), g in model._all_groups():
            if len(g) >= 8:
                out.append(dict(route_id=r, direction=d, name=g.short_name.iloc[0], n_stops=len(g),
                                first=g.stop_name.iloc[0], last=g.stop_name.iloc[-1]))
        return jsonify(out)

    @app.get("/api/examples")
    def examples():
        out = []
        for (r, d), g in model._all_groups():
            if len(g) < 12:
                continue
            a, b = g.iloc[int(len(g) * 0.15)], g.iloc[int(len(g) * 0.7)]
            out.append(dict(route=g.short_name.iloc[0], from_name=a.stop_name, from_id=a.stop_id,
                            to_name=b.stop_name, to_id=b.stop_id))
        return jsonify(out[:6])

    @app.get("/api/sms")
    def sms():
        """Plain-text answer (<=160 chars) so a basic phone can use LeaveWise through any SMS gateway."""
        try:
            o = parse_point(request.args["from"], model)
            d = parse_point(request.args["to"], model)
            date = request.args.get("date") or dt.datetime.now(IST).strftime("%Y-%m-%d")
            h, m = request.args.get("arrive_by", "09:00").split(":")
            when = dt.datetime.fromisoformat(date).replace(tzinfo=IST, hour=int(h), minute=int(m))
        except (KeyError, ValueError) as e:
            return jsonify(error=f"bad request: {e}"), 400
        res = model.journeys(o, d, when, "arrive", top=1)
        hi = request.args.get("lang") == "hi"
        if not res:
            txt = "सीधी बस नहीं मिली।" if hi else "No direct bus found."
        else:
            x = res[0]
            sure = {"high": "पक्का", "medium": "ठीक-ठाक", "low": "अनिश्चित"} if hi else {"high": "sure", "medium": "fairly sure", "low": "unsure"}
            if hi:
                txt = f"{x['leave_by']} बजे निकलें। बस {x['route']}: {x['board_stop']} से चढ़ें। सफ़र {round(x['ride_typical_min'])}-{round(x['ride_plan_min'])} मिनट। {sure[x['confidence']]}।"
            else:
                txt = (f"Leave by {x['leave_by']}. Bus {x['route']} from {x['board_stop']}. "
                       f"Ride {round(x['ride_typical_min'])}-{round(x['ride_plan_min'])} min. {sure[x['confidence']].capitalize()}.")
        return jsonify(text=txt[:160], length=len(txt[:160]))

    @app.get("/api/operator")
    def operator():
        return jsonify(model.reliability_table(request.args.get("daytype", "weekday")))

    @app.get("/api/card")
    def card():
        r, d = request.args["route_id"], int(request.args.get("direction", 0))
        i, j = int(request.args["from_stop"]), int(request.args["to_stop"])
        return jsonify(model.hourly_card(r, d, i, j, request.args.get("daytype", "weekday")))

    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="data")
    ap.add_argument("--passes", default="passes")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    create_app(Model(a.data, a.passes)).run(host="127.0.0.1", port=a.port)


if __name__ == "__main__":
    main()

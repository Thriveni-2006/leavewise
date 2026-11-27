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
    try:
        a, b = s.split(",")
        return float(a), float(b)
    except ValueError:
        hit = find_stop(s, model)
        if hit is None:
            raise ValueError(f"no stop named '{s}'")
        return float(hit.lat), float(hit.lng)


def find_stop(q, model):
    """Stop by name: exact match first, then starts-with, then contains. Ties go to the stop on more routes."""
    q = q.strip().lower()
    s = model._stops_all
    names = s.stop_name.str.lower()
    for m in (names == q, names.str.startswith(q), names.str.contains(q, regex=False)):
        hit = s[m]
        if not hit.empty:
            n = hit.stop_id.map(lambda x: len(model._stop_idx.get(x, [])))
            return hit.assign(n=n).sort_values("n", ascending=False).iloc[0]
    return None


def create_app(model):
    app = Flask(__name__, static_folder=STATIC, static_url_path="")

    @app.get("/")
    def home():
        return send_from_directory(STATIC, "index.html")

    @app.get("/pack.json")
    def pack():                       # the offline pack (python -m leavewise.export_pack)
        return send_from_directory(model.data_dir, "pack.json")

    @app.get("/pack_check.json")
    def pack_check():
        return send_from_directory(model.data_dir, "pack_check.json")

    @app.get("/api/health")
    def health():
        return jsonify(ok=True, days_of_data=len(model.dates), routes_with_gps=len({k[0] for k in model.groups}),
                       routes_in_network=len({k[0] for k in model._rd}), stops=len(model._stops_all))

    @app.get("/api/search")
    def search():
        q = request.args.get("q", "").strip().lower()
        if len(q) < 2:
            return jsonify([])
        st = model._stops_all
        nm = st.stop_name.str.lower()
        hit = st[nm.str.contains(q, regex=False)].copy()
        hit["n"] = hit.stop_id.map(lambda x: len(model._stop_idx.get(x, [])))
        hit["starts"] = hit.stop_name.str.lower().str.startswith(q)
        hit = hit.sort_values(["starts", "n"], ascending=False).head(12)
        return jsonify([dict(id=r.stop_id, name=r.stop_name, lat=r.lat, lng=r.lng, routes=int(r.n)) for r in hit.itertuples()])

    @app.get("/api/voice_match")
    def voice_match():
        """What the person SAID (one or more guesses separated by |, Hindi or English) -> best matching stops."""
        from .voice import best_matches
        qs = [x for x in request.args.get("q", "").split("|") if x.strip()]
        counts = {sid: len(v) for sid, v in model._stop_idx.items()}
        modelled = {sid for k in model.modelled for sid in model._rd[k].stop_id.values}
        st = model._stops_all[model._stops_all.stop_id.isin(modelled)] if request.args.get("gps", "1") == "1" else model._stops_all
        return jsonify(best_matches(st, counts, qs))

    @app.get("/api/stops")
    def stops():
        s = model._stops_all
        if not request.args.get("all"):      # default: only stops on routes with GPS history (keeps the page light)
            s = s[s.stop_id.isin({sid for k in model.modelled for sid in model._rd[k].stop_id.values})]
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
        live_date = date if (request.args.get("live") and date in model.dates) else None   # replay of a recorded day
        res = model.journeys(o, d, when, mode, live_date=live_date)
        return jsonify(mode=mode, when=when.isoformat(), options=res, live_replay=bool(live_date),
                       note=None if res else "No bus journey found within 800 m walk of both points (direct or with one change).")

    sp = os.path.join(model.data_dir, "shapes.csv")
    shapes = (pd.read_csv(sp, dtype={"route_id": str}) if os.path.exists(sp)
              else pd.DataFrame(columns=["route_id", "direction", "seq", "lat", "lng"]))

    @app.get("/api/shape")
    def shape():
        g = shapes[(shapes.route_id == request.args["route_id"]) & (shapes.direction == int(request.args.get("direction", 0)))]
        g = g.sort_values("seq")
        if g.empty:      # route without a drawn line: join its stops
            st = model._rd.get((request.args["route_id"], int(request.args.get("direction", 0))))
            if st is not None:
                return jsonify([[round(a, 5), round(b, 5)] for a, b in zip(st.lat, st.lng)])
        step = max(1, len(g) // 400)
        g = g.iloc[::step]
        return jsonify([[round(a, 5), round(b, 5)] for a, b in zip(g.lat, g.lng)])

    @app.get("/api/routes")
    def routes():
        out = []
        for (r, d), g in model._all_groups(modelled_only=True):
            if len(g) >= 8:
                out.append(dict(route_id=r, direction=d, name=g.short_name.iloc[0], n_stops=len(g),
                                first=g.stop_name.iloc[0], last=g.stop_name.iloc[-1]))
        return jsonify(out)

    @app.get("/api/examples")
    def examples():
        out = []
        for (r, d), g in model._all_groups(modelled_only=True):
            if len(g) < 12:
                continue
            a, b = g.iloc[int(len(g) * 0.15)], g.iloc[int(len(g) * 0.7)]
            out.append(dict(route=g.short_name.iloc[0], from_name=a.stop_name, from_id=a.stop_id,
                            to_name=b.stop_name, to_id=b.stop_id))
        return jsonify(out[:6])

    @app.get("/api/sms")
    def sms():
        """Plain-text answer (<=160 chars) so a basic phone can use Pahunch through any SMS gateway."""
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

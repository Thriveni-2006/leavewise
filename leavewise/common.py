"""Shared helpers: time (IST), day types, and projecting GPS points onto a route line."""
import datetime as dt
import math
import numpy as np

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
# Days treated like a weekend. EDIT this list after checking the Oct 2026 calendar.
HOLIDAYS = {"2026-10-02", "2026-10-20"}


def ist(ts):
    return dt.datetime.fromtimestamp(float(ts), IST)


def daytype_of_date(date_str):
    d = dt.date.fromisoformat(date_str)
    return "weekend" if d.weekday() >= 5 or date_str in HOLIDAYS else "weekday"


def hour_of(ts):
    """IST hour (0-23) for unix seconds (scalar or array)."""
    return ((np.asarray(ts, dtype=float) + 19800) // 3600 % 24).astype(int)


def haversine_m(lat1, lng1, lat2, lng2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(a))


class Polyline:
    """A route shape in local metres. project() gives distance-along-line and off-line error."""

    def __init__(self, lat, lng):
        lat = np.asarray(lat, float)
        lng = np.asarray(lng, float)
        self.lat0, self.lng0 = lat.mean(), lng.mean()
        self.x, self.y = self.xy(lat, lng)
        seg = np.hypot(np.diff(self.x), np.diff(self.y))
        self.cum = np.concatenate([[0.0], np.cumsum(seg)])
        self.length = float(self.cum[-1])

    def xy(self, lat, lng):
        k = math.cos(math.radians(self.lat0))
        return ((np.asarray(lng, float) - self.lng0) * 111320.0 * k,
                (np.asarray(lat, float) - self.lat0) * 110540.0)

    def project(self, lat, lng, chunk=400):
        px, py = self.xy(lat, lng)
        ax, ay = self.x[:-1], self.y[:-1]
        dx, dy = self.x[1:] - ax, self.y[1:] - ay
        L2 = dx * dx + dy * dy
        L2[L2 == 0] = 1e-9
        seg_len = np.sqrt(L2)
        dist = np.empty(len(px))
        res = np.empty(len(px))
        for s in range(0, len(px), chunk):
            qx, qy = px[s:s + chunk, None], py[s:s + chunk, None]
            t = np.clip(((qx - ax) * dx + (qy - ay) * dy) / L2, 0, 1)
            d2 = (qx - (ax + t * dx)) ** 2 + (qy - (ay + t * dy)) ** 2
            k = d2.argmin(1)
            r = np.arange(len(k))
            dist[s:s + chunk] = self.cum[k] + t[r, k] * seg_len[k]
            res[s:s + chunk] = np.sqrt(d2[r, k])
        return dist, res

    def point_at(self, dist):
        dist = np.clip(dist, 0, self.length)
        x = np.interp(dist, self.cum, self.x)
        y = np.interp(dist, self.cum, self.y)
        k = math.cos(math.radians(self.lat0))
        return y / 110540.0 + self.lat0, x / (111320.0 * k) + self.lng0

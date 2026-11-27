"""Voice input helper: match what a person SAYS to a bus stop name.

The browser's speech recogniser may return Hindi (Devanagari) or English (Latin) text, while stop names are in English.
So both sides are turned into a rough 'sound skeleton' (consonants only: "नेहरू प्लेस" and "Nehru Place" both become "nr pls")
and compared with a fuzzy ratio. Stops that serve more routes get a small bonus, because a person asking by voice usually
means the big, well-known stop.
"""
import re
from functools import lru_cache
from difflib import SequenceMatcher

DEV = {
    "क": "k", "ख": "k", "ग": "g", "घ": "g", "ङ": "n", "च": "c", "छ": "c", "ज": "j", "झ": "j", "ञ": "n",
    "ट": "t", "ठ": "t", "ड": "d", "ढ": "d", "ण": "n", "त": "t", "थ": "t", "द": "d", "ध": "d", "न": "n",
    "प": "p", "फ": "f", "ब": "b", "भ": "b", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "s",
    "ष": "s", "स": "s", "ह": "h", "ळ": "l", "क़": "k", "ख़": "k", "ग़": "g", "ज़": "j", "ड़": "r", "ढ़": "r", "फ़": "f",
}


@lru_cache(maxsize=None)
def skeleton(text):
    s = (text or "").lower()
    s = "".join(DEV.get(ch, "" if "ऀ" <= ch <= "ॿ" else ch) for ch in s)   # consonants of Devanagari; vowel signs dropped
    s = re.sub(r"[^a-z ]", " ", s)
    for a, b in (("ph", "f"), ("sh", "s"), ("ch", "c"), ("kh", "k"), ("gh", "g"), ("th", "t"), ("dh", "d"), ("bh", "b"), ("jh", "j")):
        s = s.replace(a, b)
    s = re.sub(r"c(?=[eiy])", "s", s)
    s = s.replace("c", "k").replace("q", "k").replace("w", "v").replace("z", "j").replace("x", "ks")
    s = re.sub(r"[aeiouyh]", "", s)
    s = re.sub(r"(.)\1+", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def _score(q, name_skel, name_low):
    a = SequenceMatcher(None, q, name_skel).ratio() if q else 0.0
    # every spoken word should be found: reward when all words of the query appear as a skeleton word of the stop
    qw = [w for w in q.split() if w]
    nw = set(name_skel.split())
    cover = (sum(1 for w in qw if w in nw) / len(qw)) if qw else 0.0
    return 0.65 * a + 0.35 * cover


def best_matches(stops, route_counts, queries, top=3):
    """stops: DataFrame with stop_id, stop_name, lat, lng. queries: several guesses of the recogniser (first = best)."""
    qs = [skeleton(q) for q in queries if q and q.strip()]
    if not qs:
        return []
    skel = [skeleton(n) for n in stops.stop_name.values]
    low = [n.lower() for n in stops.stop_name.values]
    best = {}
    for k, (sid, nm, la, ln) in enumerate(zip(stops.stop_id.values, stops.stop_name.values, stops.lat.values, stops.lng.values)):
        sc = max(_score(q, skel[k], low[k]) * (1.0 if qi == 0 else 0.97) for qi, q in enumerate(qs))
        sc += 0.04 * min(route_counts.get(sid, 0), 20) / 20.0
        # the same stop name can appear for several stop ids: keep the one on most routes
        key = nm.lower()
        if key not in best or sc > best[key][0]:
            best[key] = (sc, sid, nm, float(la), float(ln), route_counts.get(sid, 0))
    out = sorted(best.values(), key=lambda x: -x[0])[:top]
    return [dict(id=sid, name=nm, lat=la, lng=ln, routes=rc, score=round(sc, 3)) for sc, sid, nm, la, ln, rc in out if sc >= 0.45]

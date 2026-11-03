#!/usr/bin/env python3
"""
Flight Finder — cheap trips from Tokyo to Montreal and to anywhere in Asia.

Data: the free Travelpayouts (Aviasales) Data API. It returns fares that
Aviasales users found in roughly the last 48 hours, so prices are a strong
guide but must be confirmed on the booking page.

What it does that a normal search box doesn't:
  * searches every date in the next N months at once
  * builds self-transfer routes (Tokyo -> hub -> Montreal on separate tickets)
  * mixes two one-way tickets, open-jaw trips (fly into one city, out of another)
  * builds 2-stop Asia hops (Tokyo -> A -> B -> Tokyo)
  * tracks the lowest price over time and pushes alerts via ntfy (free, no account)

Usage:
  TRAVELPAYOUTS_TOKEN=xxxx python finder.py          # live
  python finder.py --fake                             # offline demo data
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
API = "https://api.travelpayouts.com"
UTC = dt.timezone.utc

# ---------------------------------------------------------------- regions
REGIONS = {
    "East Asia": "CN HK MO TW KR MN",
    "Southeast Asia": "TH VN KH LA MM MY SG ID PH BN TL",
    "South Asia": "IN LK NP BD BT MV PK",
    "Central Asia": "KZ UZ KG TJ TM",
    "Middle East": "AE QA OM SA BH KW JO IL TR GE AM AZ",
    "Japan": "JP",
}
COUNTRY_REGION = {c: r for r, cs in REGIONS.items() for c in cs.split()}

# Montreal: hubs where a self-transfer can break the trip into cheaper tickets.
PACIFIC_HUBS = ["YVR", "YYC", "SEA", "SFO", "LAX", "HNL", "YTO", "NYC", "CHI", "BOS"]
ASIA_HUBS = ["SEL", "TPE", "HKG", "MNL", "BJS", "SHA"]


# ---------------------------------------------------------------- helpers
def parse_time(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def haversine_km(a, b) -> float:
    if not a or not b:
        return 1e9
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def months_from(today: dt.date, n: int) -> list[str]:
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def full_link(link: str | None) -> str | None:
    if not link:
        return None
    if link.startswith("http"):
        return link
    if not link.startswith("/"):
        link = "/" + link
    if not link.startswith("/search"):
        link = "/search" + link
    return "https://www.aviasales.com" + link


# ---------------------------------------------------------------- API client
class Api:
    def __init__(self, token: str, pause: float):
        self.token, self.pause, self.requests, self.errors = token, pause, 0, 0

    def get(self, path: str, params: dict) -> dict:
        url = API + path + "?" + urllib.parse.urlencode(params)
        for attempt in range(4):
            req = urllib.request.Request(url, headers={
                "X-Access-Token": self.token,
                "Accept-Encoding": "gzip",
                "User-Agent": "flight-finder/1.0",
            })
            try:
                time.sleep(self.pause)
                self.requests += 1
                with urllib.request.urlopen(req, timeout=40) as r:
                    raw = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    return json.loads(raw)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep([10, 30, 60][attempt])
                    continue
                body = e.read()[:300].decode("utf-8", "replace")
                self.errors += 1
                return {"success": False, "error": f"HTTP {e.code}: {body}", "data": []}
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                if attempt < 3:
                    time.sleep(5)
                    continue
                self.errors += 1
                return {"success": False, "error": str(e), "data": []}
        return {"success": False, "error": "gave up", "data": []}

    def reference(self, name: str) -> list:
        url = f"{API}/data/en/{name}.json"
        req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "flight-finder/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw)


# ---------------------------------------------------------------- fetching
class Fetcher:
    def __init__(self, api, cfg):
        self.api, self.cfg = api, cfg
        self.cur = cfg["currency"]
        self.log: list[str] = []

    def prices(self, month: str, origin=None, destination=None, round_trip=False, market=None, pages=1):
        base = {
            "departure_at": month, "currency": self.cur, "sorting": "price",
            "unique": "false", "direct": "false", "limit": 1000,
            "one_way": "false" if round_trip else "true",
        }
        if origin:
            base["origin"] = origin
        if destination:
            base["destination"] = destination
        if market:
            base["market"] = market
        out = []
        for page in range(1, pages + 1):
            r = self.api.get("/aviasales/v3/prices_for_dates", {**base, "page": page})
            if not r.get("success") and market:
                # unknown market code -> let the API pick
                return self.prices(month, origin, destination, round_trip, None, pages)
            if not r.get("success"):
                self.log.append(f"{origin}->{destination} {month}: {r.get('error')}")
            data = r.get("data") or []
            out.extend(data)
            if len(data) < 1000:
                break
        return [o for o in (normalize(x, round_trip) for x in out) if o]


def normalize(raw: dict, round_trip: bool) -> dict | None:
    dep = parse_time(raw.get("departure_at"))
    price = raw.get("price")
    if not dep or not price or not raw.get("origin") or not raw.get("destination"):
        return None
    dur = raw.get("duration_to") or (None if round_trip else raw.get("duration")) or 0
    o = {
        "from": raw["origin"], "to": raw["destination"],
        "fa": raw.get("origin_airport") or raw["origin"],
        "ta": raw.get("destination_airport") or raw["destination"],
        "dep": dep, "arr": dep + dt.timedelta(minutes=dur) if dur else None,
        "dur": dur, "price": int(round(float(price))),
        "airline": raw.get("airline") or "",
        "flight": f'{raw.get("airline") or ""}{raw.get("flight_number") or ""}',
        "stops": int(raw.get("transfers") or 0),
        "link": full_link(raw.get("link")),
    }
    if round_trip:
        ret = parse_time(raw.get("return_at"))
        if not ret:
            return None
        o["ret"] = ret
        o["ret_stops"] = int(raw.get("return_transfers") or 0)
        o["dur_back"] = raw.get("duration_back") or 0
    return o


def cheapest_per(offers, keyfn):
    best = {}
    for o in offers:
        k = keyfn(o)
        if k not in best or o["price"] < best[k]["price"]:
            best[k] = o
    return list(best.values())


def dedupe(offers):
    return cheapest_per(offers, lambda o: (o["from"], o["to"], o["dep"].date(), o.get("ret") and o["ret"].date()))


# ---------------------------------------------------------------- trip building
def ticket_out(o):
    t = {
        "from": o["from"], "to": o["to"], "fa": o["fa"], "ta": o["ta"],
        "dep": o["dep"].isoformat(), "dur": o["dur"], "stops": o["stops"],
        "airline": o["airline"], "flight": o["flight"], "price": o["price"], "link": o["link"],
    }
    if o.get("ret"):
        t.update(ret=o["ret"].isoformat(), ret_stops=o["ret_stops"], dur_back=o["dur_back"])
    return t


def make_trip(kind, tickets, route, dest, out_dep, back_dep, **extra):
    stay = (back_dep.date() - out_dep.date()).days if back_dep else None
    trip = {
        "kind": kind, "price": sum(t["price"] for t in tickets), "route": route, "dest": dest,
        "out": out_dep.date().isoformat(), "back": back_dep.date().isoformat() if back_dep else None,
        "stay": stay, "tickets": [ticket_out(t) for t in tickets],
        "self_transfer": False,
    }
    trip.update(extra)
    trip["id"] = hashlib.sha1(json.dumps([kind, route, trip["out"], trip["back"], [t["flight"] for t in tickets]]).encode()).hexdigest()[:10]
    return trip


def bucket_of(stay, buckets):
    for i, (lo, hi) in enumerate(buckets):
        if lo <= stay <= hi:
            return i
    return None


class Best:
    """Keeps only the cheapest candidate per key; trips are built from the winners."""
    def __init__(self):
        self.d = {}

    def offer(self, key, price, make):
        cur = self.d.get(key)
        if cur is None or price < cur[0]:
            self.d[key] = (price, make)

    def trips(self):
        return [make() for _, make in self.d.values()]


def halves(direct, first_legs, second_legs, layover_h, per_day=4):
    """One direction of travel: direct one-ways plus self-transfer pairs A->H, H->B.
    Returns {date: [cheapest few halves departing that day]}."""
    lo, hi = dt.timedelta(hours=layover_h[0]), dt.timedelta(hours=layover_h[1])
    out = [{"price": o["price"], "tickets": [o], "dep": o["dep"], "arr": o["arr"] or o["dep"],
            "route": [o["from"], o["to"]], "st": False} for o in direct]
    by_hub: dict[str, list] = {}
    for b in second_legs:
        by_hub.setdefault(b["from"], []).append(b)
    for a in first_legs:
        if not a["arr"]:
            continue  # can't check the connection without a duration
        for b in by_hub.get(a["to"], []):
            gap = b["dep"] - a["arr"]
            if lo <= gap <= hi:
                out.append({"price": a["price"] + b["price"], "tickets": [a, b], "dep": a["dep"],
                            "arr": b["arr"] or b["dep"], "route": [a["from"], a["to"], b["to"]],
                            "st": True, "layover_h": round(gap.total_seconds() / 3600, 1)})
    out = cheapest_per(out, lambda h: (h["dep"].date(), tuple(h["route"])))
    out.sort(key=lambda h: h["price"])
    by_day: dict[dt.date, list] = {}
    for h in out:
        lst = by_day.setdefault(h["dep"].date(), [])
        # keep the cheapest few, but always keep the cheapest no-self-transfer option too
        if len(lst) < per_day or (not h["st"] and all(x["st"] for x in lst)):
            lst.append(h)
    return by_day


def build_montreal(cfg, f: dict, home: str, cities: dict | None = None):
    name = lambda c: (cities or {}).get(c, {}).get("name", c)
    mc = cfg["montreal"]
    M = mc["city"]
    s_lo, s_hi = mc["stay_days"]
    buckets = mc["stay_buckets"]
    best = Best()

    # 1) real round-trip tickets
    for o in f["mtl_rt"]:
        stay = (o["ret"].date() - o["dep"].date()).days
        b = bucket_of(stay, buckets)
        if b is not None:
            best.offer(("RT", o["dep"].date(), o["ret"].date()), o["price"],
                       lambda o=o: make_trip("Round trip", [o], [home, M, home], M, o["dep"], o["ret"]))

    # 2) one-way halves, direct or via a hub on separate tickets
    out_h = halves([o for o in f["out_1w"] if o["from"] == home and o["to"] == M],
                   [o for o in f["out_1w"] if o["from"] == home and o["to"] != M],
                   [o for o in f["in_mtl"] if o["to"] == M and o["from"] != home],
                   mc["self_transfer_layover_hours"])
    back_h = halves([o for o in f["back_1w"] if o["from"] == M and o["to"] == home],
                    [o for o in f["back_1w"] if o["from"] == M and o["to"] != home],
                    [o for o in f["in_home"] if o["to"] == home and o["from"] != M],
                    mc["self_transfer_layover_hours"])
    for day, outs in out_h.items():
        for a in outs:
            land = a["arr"].date()
            for stay in range(s_lo, s_hi + 1):
                if bucket_of(stay, buckets) is None:
                    continue
                for b in back_h.get(land + dt.timedelta(days=stay), []):
                    st = a["st"] or b["st"]
                    kind = "Self-transfer" if st else "Two one-ways"

                    def mk(a=a, b=b, st=st, kind=kind):
                        notes = [f'{h["layover_h"]}h self-transfer in {name(h["route"][1])}' for h in (a, b) if h["st"]]
                        return make_trip(kind, a["tickets"] + b["tickets"], a["route"] + b["route"][1:], M,
                                         a["dep"], b["dep"], self_transfer=st, note="; ".join(notes) or None)
                    best.offer((kind, day, b["dep"].date()), a["price"] + b["price"], mk)

    # 3) nearby airports with ground transport
    for o in f["nearby_rt"]:
        stay = (o["ret"].date() - o["dep"].date()).days
        if bucket_of(stay, buckets) is not None and o["to"] in mc["nearby_airports"]:
            best.offer(("NB", o["to"], o["dep"].date(), o["ret"].date()), o["price"],
                       lambda o=o: make_trip("Nearby airport", [o], [home, o["to"], home], M, o["dep"], o["ret"],
                                             note=mc["nearby_airports"][o["to"]]))

    trips = sorted(best.trips(), key=lambda t: t["price"])
    return select(trips, lambda t: (t["out"][:7], bucket_of(t["stay"], buckets), t["kind"]), 8,
                  cfg["max_trips_kept"]["montreal"])


def build_asia(cfg, f: dict, cities: dict, home: str, firsts: list):
    ac = cfg["asia"]
    s_lo, s_hi = ac["stay_days"]
    buckets = ac["stay_buckets"]
    d_lo, d_hi = ac["hop"]["days_per_stop"]
    one = dt.timedelta(days=1)

    def ok(code):
        c = cities.get(code)
        if not c or code == home or c["region"] is None:
            return False
        return ac["include_japan_domestic"] or c["region"] != "Japan"

    out_1w = cheapest_per([o for o in f["out_1w"] if o["from"] == home and ok(o["to"])],
                          lambda o: (o["to"], o["dep"].date()))
    in_idx = {(o["from"], o["dep"].date()): o for o in
              cheapest_per([o for o in f["in_home"] if o["to"] == home and ok(o["from"])],
                           lambda o: (o["from"], o["dep"].date()))}
    in_cities = {c for c, _ in in_idx}
    best = Best()

    def offer(kind, route, out_dep, back_dep, price, mk):
        b = bucket_of((back_dep.date() - out_dep.date()).days, buckets)
        if b is not None:
            best.offer((kind, tuple(route), out_dep.date().isoformat()[:7], b, out_dep.date().isocalendar()[1]),
                       price, mk)

    for o in f["home_rt"]:
        if o["from"] == home and ok(o["to"]):
            offer("RT", [home, o["to"], home], o["dep"], o["ret"], o["price"],
                  lambda o=o: make_trip("Round trip", [o], [home, o["to"], home], o["to"], o["dep"], o["ret"]))

    # two one-ways + open-jaw
    near = {}
    for a in {o["to"] for o in out_1w}:
        near[a] = [b for b in in_cities if b == a or
                   haversine_km(cities[a]["xy"], cities[b]["xy"]) <= ac["open_jaw_max_km"]]
    for a in out_1w:
        land = (a["arr"] or a["dep"]).date()
        for b_city in near.get(a["to"], []):
            for stay in range(s_lo, s_hi + 1):
                b = in_idx.get((b_city, land + stay * one))
                if not b:
                    continue
                if b_city == a["to"]:
                    offer("2OW", [home, a["to"], home], a["dep"], b["dep"], a["price"] + b["price"],
                          lambda a=a, b=b: make_trip("Two one-ways", [a, b], [home, a["to"], home], a["to"],
                                                     a["dep"], b["dep"]))
                else:
                    def mk(a=a, b=b, bc=b_city):
                        km = round(haversine_km(cities[a["to"]]["xy"], cities[bc]["xy"]))
                        return make_trip("Open-jaw", [a, b], [home, a["to"], bc, home], a["to"], a["dep"], b["dep"],
                                         gap_km=km, note=f"Make your own way {cities[a['to']]['name']} → "
                                                         f"{cities[bc]['name']} (~{km} km)")
                    offer("OJ", [home, a["to"], b_city, home], a["dep"], b["dep"], a["price"] + b["price"], mk)

    # 2-stop hops: home -> A -> B -> home
    mid_idx: dict = {}
    for m in cheapest_per([m for m in f["hop_mid"] if ok(m["to"]) and m["to"] != m["from"] and m["from"] in firsts],
                          lambda o: (o["from"], o["to"], o["dep"].date())):
        mid_idx.setdefault((m["from"], m["dep"].date()), []).append(m)
    for a in out_1w:
        if a["to"] not in firsts:
            continue
        land_a = (a["arr"] or a["dep"]).date()
        for da in range(d_lo, d_hi + 1):
            for m in mid_idx.get((a["to"], land_a + da * one), []):
                land_b = (m["arr"] or m["dep"]).date()
                for db in range(d_lo, d_hi + 1):
                    b = in_idx.get((m["to"], land_b + db * one))
                    if not b or (b["dep"].date() - a["dep"].date()).days > s_hi:
                        continue

                    def mk(a=a, m=m, b=b, da=da, db=db):
                        return make_trip("Multi-city", [a, m, b], [home, a["to"], m["to"], home], a["to"],
                                         a["dep"], b["dep"],
                                         note=f"{da} days in {cities[a['to']]['name']}, then {db} in "
                                              f"{cities[m['to']]['name']}")
                    offer("MC", [home, a["to"], m["to"], home], a["dep"], b["dep"],
                          a["price"] + m["price"] + b["price"], mk)

    trips = sorted(best.trips(), key=lambda t: t["price"])
    return select(trips, lambda t: (t["dest"], t["out"][:7], bucket_of(t["stay"], buckets)), 2,
                  cfg["max_trips_kept"]["asia"])


def select(trips, keyfn, per_key, cap):
    """Keep the best few per group first so one cheap city or month can't crowd out the rest."""
    seen, first, rest = {}, [], []
    for t in trips:  # already sorted by price
        k = keyfn(t)
        seen[k] = seen.get(k, 0) + 1
        (first if seen[k] <= per_key else rest).append(t)
    keep = first[:cap] + rest[: max(0, cap - len(first))]
    keep.sort(key=lambda t: t["price"])
    return keep


# ---------------------------------------------------------------- reference data
def load_cities(api) -> dict:
    cache = ROOT / ".cache" / "cities.json"
    try:
        rows = api.reference("cities")
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps(rows))
    except Exception:
        rows = json.loads(cache.read_text()) if cache.exists() else []
    out = {}
    for r in rows:
        code = r.get("code")
        if not code:
            continue
        xy = r.get("coordinates") or {}
        name = (r.get("name_translations") or {}).get("en") or r.get("name") or code
        cc = r.get("country_code")
        out[code] = {"name": name, "country": cc, "region": COUNTRY_REGION.get(cc),
                     "xy": (xy.get("lat"), xy.get("lon")) if xy.get("lat") is not None else None}
    return out


def load_airlines(api) -> dict:
    try:
        rows = api.reference("airlines")
    except Exception:
        return {}
    return {r["code"]: (r.get("name_translations") or {}).get("en") or r.get("name") or r["code"]
            for r in rows if r.get("code")}


def load_countries(api) -> dict:
    try:
        rows = api.reference("countries")
    except Exception:
        return {}
    return {r["code"]: (r.get("name_translations") or {}).get("en") or r.get("name") or r["code"]
            for r in rows if r.get("code")}


# ---------------------------------------------------------------- history + alerts
def update_history(path: Path, today: str, mtl, asia):
    h = json.loads(path.read_text()) if path.exists() else {"series": {}, "alerted": {}}
    series = h.setdefault("series", {})

    def put(key, price):
        s = series.setdefault(key, {})
        s[today] = min(price, s.get(today, price))

    real = [t for t in mtl if t["kind"] != "Nearby airport"]
    if real:
        put("montreal", real[0]["price"])
        for t in real:
            put(f"montreal:{t['out'][:7]}", t["price"])
    for t in asia:
        put(f"asia:{t['dest']}", t["price"])
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=180)).isoformat()
    for k in list(series):
        series[k] = {d: p for d, p in sorted(series[k].items()) if d >= cutoff}
    return h


def pick_alerts(cfg, h, today, mtl, asia, cities):
    alerted = h.setdefault("alerted", {})
    msgs = []

    def check(key, trip, limit, label):
        if not trip or trip["price"] > limit:
            return
        prev = alerted.get(key)
        fresh = prev and (dt.date.fromisoformat(today) - dt.date.fromisoformat(prev["date"])).days <= 30
        if fresh and trip["price"] >= prev["price"] * 0.95:
            return
        alerted[key] = {"price": trip["price"], "date": today}
        msgs.append(f"{label}: ¥{trip['price']:,} · {trip['out']} → {trip['back']} · {trip['kind']}")

    real = [t for t in mtl if t["kind"] != "Nearby airport"]
    check("montreal", real[0] if real else None, cfg["montreal"]["alert_below"], "Tokyo ⇄ Montreal")
    best = {}
    for t in asia:
        best.setdefault(t["dest"], t)
    for dest, t in sorted(best.items(), key=lambda kv: kv[1]["price"])[:8]:
        check(f"asia:{dest}", t, cfg["asia"]["alert_below"], f"Tokyo ⇄ {cities.get(dest, {}).get('name', dest)}")
    return msgs


def send_ntfy(topic: str, msgs: list[str], page_url: str | None):
    if not topic or not msgs:
        return
    headers = {"Title": "Cheap flights found", "Tags": "airplane", "Priority": "default"}
    if page_url:
        headers["Click"] = page_url
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data="\n".join(msgs).encode(), headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print("ntfy failed:", e, file=sys.stderr)


# ---------------------------------------------------------------- main
def fetch_all(fx: Fetcher, cfg, months, home):
    M = cfg["montreal"]["city"]
    f = {k: [] for k in ["mtl_rt", "out_1w", "back_1w", "in_mtl", "in_home", "home_rt", "nearby_rt", "hop_mid"]}
    for mo in months:
        print("month", mo, file=sys.stderr)
        # Asia-wide, and Asian hubs for Montreal self-transfers
        f["out_1w"] += fx.prices(mo, origin=home, market="jp", pages=3)
        f["in_home"] += fx.prices(mo, destination=home, market="jp", pages=3)
        f["home_rt"] += fx.prices(mo, origin=home, round_trip=True, market="jp", pages=2)
        # Montreal direct
        f["mtl_rt"] += fx.prices(mo, origin=home, destination=M, round_trip=True, market="jp")
        f["out_1w"] += fx.prices(mo, origin=home, destination=M, market="jp")
        f["back_1w"] += fx.prices(mo, origin=M, destination=home)
        # cheap ways into / out of Montreal from anywhere (domestic + US hops)
        f["in_mtl"] += fx.prices(mo, destination=M, market="ca", pages=2)
        f["back_1w"] += fx.prices(mo, origin=M, pages=2)
        # transpacific legs to hubs, and Asian hubs to Montreal
        for hub in PACIFIC_HUBS:
            f["out_1w"] += fx.prices(mo, origin=home, destination=hub, market="jp")
            f["in_home"] += fx.prices(mo, origin=hub, destination=home)
        for hub in ASIA_HUBS:
            f["in_mtl"] += fx.prices(mo, origin=hub, destination=M)
            f["back_1w"] += fx.prices(mo, origin=M, destination=hub)
        for ap in cfg["montreal"]["nearby_airports"]:
            f["nearby_rt"] += fx.prices(mo, origin=home, destination=ap, round_trip=True, market="jp")
    return {k: dedupe(v) for k, v in f.items()}


def fetch_hops(fx: Fetcher, cfg, months, f, cities, home):
    """For the cheapest first stops, pull onward flights from there to anywhere."""
    best = {}
    for o in f["out_1w"]:
        c = cities.get(o["to"])
        if c and c["region"] and c["region"] != "Japan" and o["to"] != home:
            best[o["to"]] = min(best.get(o["to"], 1e12), o["price"])
    firsts = [c for c, _ in sorted(best.items(), key=lambda kv: kv[1])][: cfg["asia"]["hop"]["first_stops"]]
    mids = []
    for mo in months:
        for c in firsts:
            mids += fx.prices(mo, origin=c)
    return dedupe(mids), firsts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="use generated demo data, no network")
    ap.add_argument("--no-alerts", action="store_true")
    args = ap.parse_args(argv)

    cfg = json.loads((ROOT / "config.json").read_text())
    home = cfg["home"]
    today = dt.date.today()
    months = months_from(today, cfg["months_ahead"])

    if args.fake:
        sys.path.insert(0, str(ROOT / "tests"))
        from fake_api import FakeApi
        api = FakeApi(today)
    else:
        token = os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip()
        if not token:
            sys.exit("Set TRAVELPAYOUTS_TOKEN (free at travelpayouts.com) or run with --fake")
        api = Api(token, cfg["request_pause_seconds"])

    cities = load_cities(api)
    airlines = load_airlines(api)
    countries = load_countries(api)
    fx = Fetcher(api, cfg)
    f = fetch_all(fx, cfg, months, home)
    f["hop_mid"], firsts = fetch_hops(fx, cfg, months, f, cities, home)
    if api.requests and api.errors >= api.requests * 0.8:
        print("\n".join(fx.log[:5]), file=sys.stderr)
        sys.exit("Most API requests failed — check the TRAVELPAYOUTS_TOKEN secret. Page left unchanged.")
    # drop anything in the past
    now = dt.datetime.now(UTC)
    for k in f:
        f[k] = [o for o in f[k] if o["dep"] > now]

    for c in {o["from"] for v in f.values() for o in v} | {o["to"] for v in f.values() for o in v}:
        cities.setdefault(c, {"name": c, "country": None, "region": None, "xy": None})

    mtl = build_montreal(cfg, f, home, cities)
    asia = build_asia(cfg, f, cities, home, firsts)

    DOCS.mkdir(exist_ok=True)
    hist_path = DOCS / "history.json"
    h = update_history(hist_path, today.isoformat(), mtl, asia)
    msgs = pick_alerts(cfg, h, today.isoformat(), mtl, asia, cities)
    hist_path.write_text(json.dumps(h, separators=(",", ":")))

    used = {c for t in mtl + asia for c in t["route"]} | {tk[k] for t in mtl + asia for tk in t["tickets"] for k in ("fa", "ta")}
    used_air = {tk["airline"] for t in mtl + asia for tk in t["tickets"]}
    out = {
        "generated_at": dt.datetime.now(UTC).isoformat(timespec="seconds"),
        "demo": bool(args.fake),
        "currency": cfg["currency"].upper(), "home": home, "montreal_code": cfg["montreal"]["city"],
        "config": {"montreal": {k: cfg["montreal"][k] for k in ("stay_days", "alert_below")},
                   "asia": {k: cfg["asia"][k] for k in ("stay_days", "alert_below")}},
        "cities": {c: {"name": cities[c]["name"], "country": countries.get(cities[c]["country"], cities[c]["country"]),
                       "region": cities[c]["region"]} for c in sorted(used) if c in cities},
        "airlines": {a: airlines.get(a, a) for a in sorted(used_air) if a},
        "montreal": mtl, "asia": asia,
        "history": {k: sorted(v.items())[-90:] for k, v in h["series"].items()},
        "stats": {"requests": api.requests, "errors": getattr(api, "errors", 0),
                  "offers": sum(len(v) for v in f.values()), "hop_first_stops": firsts},
    }
    (DOCS / "data.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"montreal trips: {len(mtl)}  asia trips: {len(asia)}  requests: {api.requests}  errors: {out['stats']['errors']}")
    for line in fx.log[:10]:
        print("  warn:", line, file=sys.stderr)
    if msgs:
        print("alerts:\n  " + "\n  ".join(msgs))
    if not args.no_alerts and not args.fake:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        page = os.environ.get("PAGE_URL") or (f"https://{repo.split('/')[0]}.github.io/{repo.split('/')[1]}/" if "/" in repo else None)
        send_ntfy(os.environ.get("NTFY_TOPIC", "").strip(), msgs, page)


if __name__ == "__main__":
    main()

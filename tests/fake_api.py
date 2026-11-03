"""Offline stand-in for the Travelpayouts Data API, for demos and tests.

Produces plausible (made-up) fares with the same field names as the real
/aviasales/v3/prices_for_dates endpoint, so the whole pipeline can run
without a token or network access.
"""
import calendar
import datetime as dt
import hashlib
import math
import random

#        code   name            country  lat      lon      utc offset (h)
CITIES = """
TYO Tokyo JP 35.68 139.76 9
OSA Osaka JP 34.69 135.50 9
OKA Okinawa JP 26.21 127.68 9
SEL Seoul KR 37.56 126.98 9
PUS Busan KR 35.18 129.08 9
TPE Taipei TW 25.03 121.56 8
KHH Kaohsiung TW 22.63 120.30 8
HKG Hong_Kong HK 22.32 114.17 8
MFM Macau MO 22.20 113.54 8
SHA Shanghai CN 31.23 121.47 8
BJS Beijing CN 39.90 116.40 8
BKK Bangkok TH 13.75 100.50 7
CNX Chiang_Mai TH 18.79 98.98 7
HKT Phuket TH 7.88 98.39 7
SGN Ho_Chi_Minh_City VN 10.82 106.63 7
HAN Hanoi VN 21.03 105.85 7
DAD Da_Nang VN 16.05 108.20 7
MNL Manila PH 14.60 120.98 8
CEB Cebu PH 10.31 123.89 8
SIN Singapore SG 1.35 103.82 8
KUL Kuala_Lumpur MY 3.14 101.69 8
PEN Penang MY 5.41 100.33 8
DPS Bali ID -8.65 115.22 8
JKT Jakarta ID -6.21 106.85 7
REP Siem_Reap KH 13.36 103.86 7
PNH Phnom_Penh KH 11.56 104.92 7
DEL Delhi IN 28.61 77.21 5.5
KTM Kathmandu NP 27.72 85.32 5.75
CMB Colombo LK 6.93 79.85 5.5
ULN Ulaanbaatar MN 47.89 106.91 8
DXB Dubai AE 25.20 55.27 4
YMQ Montreal CA 45.50 -73.57 -5
YOW Ottawa CA 45.42 -75.70 -5
YTO Toronto CA 43.65 -79.38 -5
YVR Vancouver CA 49.28 -123.12 -8
YYC Calgary CA 51.05 -114.07 -7
SEA Seattle US 47.61 -122.33 -8
SFO San_Francisco US 37.77 -122.42 -8
LAX Los_Angeles US 34.05 -118.24 -8
HNL Honolulu US 21.31 -157.86 -10
NYC New_York US 40.71 -74.01 -5
BOS Boston US 42.36 -71.06 -5
CHI Chicago US 41.88 -87.63 -6
PBG Plattsburgh US 44.70 -73.45 -5
BTV Burlington US 44.48 -73.21 -5
"""
CITY = {}
for line in CITIES.strip().splitlines():
    c, n, cc, lat, lon, off = line.split()
    CITY[c] = dict(name=n.replace("_", " "), cc=cc, lat=float(lat), lon=float(lon), off=float(off))

AIRLINES = {
    "NH": "ANA", "JL": "Japan Airlines", "MM": "Peach", "ZG": "ZIPAIR", "GK": "Jetstar Japan",
    "7C": "Jeju Air", "TW": "T'way Air", "IT": "Tigerair Taiwan", "CI": "China Airlines", "BR": "EVA Air",
    "CX": "Cathay Pacific", "UO": "HK Express", "FD": "Thai AirAsia", "VJ": "VietJet Air",
    "5J": "Cebu Pacific", "TR": "Scoot", "AK": "AirAsia", "AC": "Air Canada", "WS": "WestJet",
    "F8": "Flair Airlines", "PD": "Porter Airlines", "UA": "United", "DL": "Delta", "B6": "JetBlue",
    "KE": "Korean Air", "MU": "China Eastern", "EK": "Emirates", "AI": "Air India",
}
CARRIERS = {
    "JP": ["NH", "JL", "MM", "ZG", "GK"], "KR": ["7C", "TW", "KE"], "TW": ["IT", "CI", "BR"],
    "HK": ["CX", "UO"], "MO": ["UO"], "CN": ["MU"], "TH": ["FD"], "VN": ["VJ"], "PH": ["5J"],
    "SG": ["TR"], "MY": ["AK"], "ID": ["AK"], "KH": ["VJ"], "IN": ["AI"], "NP": ["AI"], "LK": ["TR"],
    "MN": ["MU"], "AE": ["EK"], "CA": ["AC", "WS", "F8", "PD"], "US": ["UA", "DL", "B6"],
}
NA = {"CA", "US"}


def km(a, b):
    A, B = CITY[a], CITY[b]
    la1, lo1, la2, lo2 = map(math.radians, (A["lat"], A["lon"], B["lat"], B["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def plausible(o, d):
    """Skip routes nobody would fly as one ticket in this toy world."""
    if o == d:
        return False
    a, b = CITY[o]["cc"], CITY[d]["cc"]
    if a in NA and b in NA:
        return True
    if a in NA or b in NA:  # transpacific: only from big Asian cities
        asian = o if b in NA else d
        return asian in {"TYO", "OSA", "SEL", "TPE", "HKG", "MNL", "SHA", "BJS"}
    return True


class FakeApi:
    def __init__(self, today: dt.date):
        self.today, self.requests, self.errors = today, 0, 0

    def reference(self, name):
        if name == "cities":
            return [{"code": c, "name": v["name"], "name_translations": {"en": v["name"]}, "country_code": v["cc"],
                     "coordinates": {"lat": v["lat"], "lon": v["lon"]}} for c, v in CITY.items()]
        if name == "airlines":
            return [{"code": c, "name": n, "name_translations": {"en": n}} for c, n in AIRLINES.items()]
        if name == "countries":
            names = {"JP": "Japan", "KR": "South Korea", "TW": "Taiwan", "HK": "Hong Kong", "MO": "Macau",
                     "CN": "China", "TH": "Thailand", "VN": "Vietnam", "PH": "Philippines", "SG": "Singapore",
                     "MY": "Malaysia", "ID": "Indonesia", "KH": "Cambodia", "IN": "India", "NP": "Nepal",
                     "LK": "Sri Lanka", "MN": "Mongolia", "AE": "United Arab Emirates", "CA": "Canada",
                     "US": "United States"}
            return [{"code": c, "name": n} for c, n in names.items()]
        return []

    def get(self, path, p):
        self.requests += 1
        if int(p.get("page", 1)) > 1:
            return {"success": True, "data": []}
        o, d = p.get("origin"), p.get("destination")
        y, m = map(int, p["departure_at"].split("-")[:2])
        rt = p.get("one_way") == "false"
        pairs = [(o, d)] if o and d else ([(o, x) for x in CITY] if o else [(x, d) for x in CITY])
        rows = []
        for a, b in pairs:
            if a not in CITY or b not in CITY or not plausible(a, b):
                continue
            for day in range(1, calendar.monthrange(y, m)[1] + 1):
                date = dt.date(y, m, day)
                if date <= self.today:
                    continue
                rows += self._offers(a, b, date, rt)
        rows.sort(key=lambda r: r["price"])
        return {"success": True, "data": rows[: int(p.get("limit", 30))], "currency": p.get("currency")}

    def _offers(self, a, b, date, rt):
        seed = int(hashlib.md5(f"{a}{b}{date}{rt}".encode()).hexdigest()[:8], 16)
        r = random.Random(seed)
        if r.random() > 0.55:  # nobody searched this day recently
            return []
        dist = km(a, b)
        price = (3000 + dist * 8) * r.uniform(0.6, 1.35)
        if {a, b} == {"TYO", "YVR"} or {a, b} == {"TYO", "HNL"}:
            price *= 0.7  # low-cost transpacific
        if CITY[a]["cc"] == "CA" and CITY[b]["cc"] == "CA":
            price *= 0.8
        stops = 0 if dist < 5500 or r.random() < 0.3 else 1
        if a in ("YMQ",) or b in ("YMQ",):
            stops = max(stops, 0 if dist < 5000 else 1)
        dur = int(dist / 800 * 60 + 40 + stops * r.randint(90, 240))
        hh, mm = r.randint(6, 22), r.choice([0, 15, 30, 45])
        off = CITY[a]["off"]
        tz = dt.timezone(dt.timedelta(hours=off))
        dep = dt.datetime(date.year, date.month, date.day, hh, mm, tzinfo=tz)
        carrier = r.choice(CARRIERS.get(CITY[a]["cc"], ["NH"]) + CARRIERS.get(CITY[b]["cc"], []))
        row = {
            "origin": a, "destination": b, "origin_airport": a, "destination_airport": b,
            "price": int(price), "airline": carrier, "flight_number": str(r.randint(10, 999)),
            "departure_at": dep.isoformat(), "transfers": stops, "duration": dur, "duration_to": dur,
            "link": f"/search/{a}{date:%d%m}{b}1?t=demo",
        }
        if rt:
            long = CITY[a]["cc"] in NA or CITY[b]["cc"] in NA
            stay = r.randint(7, 45) if long else r.randint(3, 21)
            ret = dep + dt.timedelta(days=stay, hours=r.randint(-6, 6))
            back = int(dist / 800 * 60 + 40 + stops * 120)
            row.update(price=int(price * r.uniform(1.45, 1.9)), return_at=ret.isoformat(),
                       return_transfers=stops, duration_back=back, duration=dur + back,
                       link=f"/search/{a}{date:%d%m}{b}{ret:%d%m}1?t=demo")
        return [row]

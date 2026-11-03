"""Run: python -m unittest discover tests"""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import finder  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
EST = dt.timezone(dt.timedelta(hours=-5))
PST = dt.timezone(dt.timedelta(hours=-8))


def raw(o, d, when, price, dur, rt=None):
    r = {"origin": o, "destination": d, "price": price, "departure_at": when.isoformat(),
         "duration_to": dur, "duration": dur, "transfers": 0, "airline": "XX", "flight_number": "1",
         "link": f"/search/{o}{d}"}
    if rt:
        r.update(return_at=rt.isoformat(), return_transfers=0, duration_back=dur)
    return finder.normalize(r, bool(rt))


CFG = json.loads((ROOT / "config.json").read_text())


def empty():
    return {k: [] for k in ["mtl_rt", "out_1w", "back_1w", "in_mtl", "in_home", "home_rt", "nearby_rt", "hop_mid"]}


class Montreal(unittest.TestCase):
    def test_self_transfer_layover_window(self):
        f = empty()
        a = raw("TYO", "YVR", dt.datetime(2027, 1, 10, 17, 0, tzinfo=JST), 40000, 9 * 60)   # lands 01:00 UTC 10 Jan... +9h
        ok = raw("YVR", "YMQ", dt.datetime(2027, 1, 10, 16, 0, tzinfo=PST), 20000, 300)       # 15h after landing
        tight = raw("YVR", "YMQ", dt.datetime(2027, 1, 10, 11, 30, tzinfo=PST), 10000, 300)   # 1.5h after landing
        back = raw("YMQ", "TYO", dt.datetime(2027, 1, 30, 12, 0, tzinfo=EST), 60000, 13 * 60)
        f["out_1w"] = [a]
        f["in_mtl"] = [ok, tight]
        f["back_1w"] = [back]
        trips = finder.build_montreal(CFG, f, "TYO")
        self.assertEqual(len(trips), 1)
        t = trips[0]
        self.assertEqual(t["kind"], "Self-transfer")
        self.assertEqual(t["price"], 120000)
        self.assertEqual(t["route"], ["TYO", "YVR", "YMQ", "TYO"])
        self.assertTrue(t["self_transfer"])

    def test_stay_limits(self):
        f = empty()
        dep = dt.datetime(2027, 2, 1, 18, 0, tzinfo=JST)
        f["mtl_rt"] = [raw("TYO", "YMQ", dep, 150000, 780, rt=dep + dt.timedelta(days=3)),
                       raw("TYO", "YMQ", dep, 160000, 780, rt=dep + dt.timedelta(days=20))]
        trips = finder.build_montreal(CFG, f, "TYO")
        self.assertEqual([t["stay"] for t in trips], [20])


class Asia(unittest.TestCase):
    cities = {
        "TYO": {"name": "Tokyo", "region": "Japan", "xy": (35.68, 139.76)},
        "BKK": {"name": "Bangkok", "region": "Southeast Asia", "xy": (13.75, 100.5)},
        "CNX": {"name": "Chiang Mai", "region": "Southeast Asia", "xy": (18.79, 98.98)},
        "SIN": {"name": "Singapore", "region": "Southeast Asia", "xy": (1.35, 103.82)},
        "OKA": {"name": "Okinawa", "region": "Japan", "xy": (26.2, 127.7)},
    }

    def test_open_jaw_and_hop(self):
        f = empty()
        d = dt.datetime(2027, 3, 1, 10, 0, tzinfo=JST)
        f["out_1w"] = [raw("TYO", "BKK", d, 20000, 400), raw("TYO", "OKA", d, 5000, 150)]
        f["in_home"] = [raw("CNX", "TYO", d + dt.timedelta(days=8), 22000, 380),
                        raw("SIN", "TYO", d + dt.timedelta(days=9), 25000, 420)]
        f["hop_mid"] = [raw("BKK", "SIN", d + dt.timedelta(days=4), 6000, 140)]
        trips = finder.build_asia(CFG, f, self.cities, "TYO", ["BKK"])
        kinds = {t["kind"]: t for t in trips}
        self.assertIn("Open-jaw", kinds)                   # BKK in, CNX out (~580 km)
        self.assertEqual(kinds["Open-jaw"]["route"], ["TYO", "BKK", "CNX", "TYO"])
        self.assertIn("Multi-city", kinds)                 # TYO-BKK-SIN-TYO
        self.assertEqual(kinds["Multi-city"]["price"], 51000)
        self.assertNotIn("OKA", {t["dest"] for t in trips})  # domestic excluded by default
        # Singapore is too far from Bangkok for an open-jaw
        self.assertFalse(any(t["route"] == ["TYO", "BKK", "SIN", "TYO"] and t["kind"] == "Open-jaw" for t in trips))


class FakeRun(unittest.TestCase):
    def test_end_to_end_demo(self):
        finder.main(["--fake", "--no-alerts"])
        data = json.loads((ROOT / "docs" / "data.json").read_text())
        self.assertTrue(data["montreal"] and data["asia"])
        prices = [t["price"] for t in data["asia"]]
        self.assertEqual(prices, sorted(prices))
        for t in data["montreal"] + data["asia"]:
            self.assertEqual(t["price"], sum(k["price"] for k in t["tickets"]))


if __name__ == "__main__":
    unittest.main()

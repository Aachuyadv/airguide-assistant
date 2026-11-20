import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from airguide.data_source import clear_caches, fetch_live, fetch_live_coords
from airguide.server import RateLimiter, build_handler
from tests.fakes import open_meteo


class LiveApiTests(unittest.TestCase):
    httpd = None
    ok = False

    @classmethod
    def setUpClass(cls):
        clear_caches()
        f = open_meteo()
        handler = build_handler(
            live_fetch=lambda c: fetch_live(c, f),
            coords_fetch=lambda la, lo: fetch_live_coords(la, lo, fetcher=f),
            use_llm=False,
            limiter=RateLimiter(100, 60),
        )
        try:
            cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
            threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
            urllib.request.urlopen(cls.base + "/healthz", timeout=2).read()
            cls.ok = True
        except OSError:
            cls.httpd = None

    @classmethod
    def tearDownClass(cls):
        if cls.httpd:
            cls.httpd.shutdown(); cls.httpd.server_close()

    def setUp(self):
        if not self.ok:
            self.skipTest("loopback sockets unavailable")

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, json.loads(r.read()), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), e.headers

    def post(self, body):
        req = urllib.request.Request(self.base + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def test_live_by_city(self):
        status, body, headers = self.get("/api/live?city=Delhi")
        self.assertEqual(status, 200)
        live = body["live"]
        self.assertEqual((live["place"], live["aqi"], len(live["forecast"])), ("Delhi, India", 168, 24))
        self.assertIn("no-store", headers["Cache-Control"])

    def test_live_by_coords_and_validation(self):
        self.assertEqual(self.get("/api/live?lat=28.6&lon=77.2")[0], 200)
        for bad in ("/api/live", "/api/live?lat=95&lon=0", "/api/live?lat=x&lon=1", "/api/live?city=%3C%3E%21%21"):
            self.assertEqual(self.get(bad)[0], 400, bad)

    def test_live_unknown_city_404(self):
        self.assertEqual(self.get("/api/live?city=zzz")[0], 404)

    def test_geolocation_header(self):
        self.assertIn("geolocation=(self)", self.get("/healthz")[2]["Permissions-Policy"])

    def test_chat_returns_live_payload_and_coords_flow(self):
        body = self.post({"message": "air quality in Delhi", "state": None})
        self.assertEqual(body["live"]["aqi"], 168)
        body = self.post({"message": "at my location", "state": None, "coords": {"lat": 28.6, "lon": 77.2}})
        self.assertEqual(body["state"]["location"], "Your location")
        self.assertEqual(body["live"]["place"], "Your location")
        # junk coords are ignored, not crashed on
        body = self.post({"message": "hello", "state": None, "coords": {"lat": "x", "lon": 999}})
        self.assertIsNone(body["live"])

    def test_saved_profile_skips_sensitivity_question(self):
        body = self.post({"message": "air quality in Delhi", "state": {"sensitivity": ["lung"]}})
        self.assertEqual(body["state"]["awaiting"], "activity")
        self.assertEqual(body["state"]["sensitivity"], ["lung"])
        body = self.post({"message": "air quality in Delhi", "state": {"sensitivity": ["bogus", "heart"]}})
        self.assertEqual(body["state"]["sensitivity"], ["heart"])  # unknown groups are dropped

    def test_chat_without_live_has_null(self):
        self.assertIsNone(self.post({"message": "AQI is 40", "state": None})["live"])


if __name__ == "__main__":
    unittest.main()

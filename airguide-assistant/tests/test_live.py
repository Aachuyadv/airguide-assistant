import time
import unittest

from airguide.assistant import respond
from airguide.context import UserContext
from unittest import mock

from airguide.data_source import (
    DataSourceError, LocationNotFound, cache_stats, clear_caches, fetch_live, fetch_live_coords, valid_coords,
)
from airguide.insights import Window, best_window, fmt_hour, fmt_window, trend

from tests.fakes import hourly_series, open_meteo


class ParseTests(unittest.TestCase):
    def setUp(self):
        clear_caches()

    def test_full_reading(self):
        r = fetch_live("Delhi", open_meteo())
        self.assertEqual((r.place, r.aqi, r.pm25, r.pm10), ("Delhi, India", 168, 80.0, 140.0))
        self.assertEqual((r.ozone, r.no2, r.so2, r.co), (45.0, 38.5, 12.0, 640.0))
        self.assertEqual(r.observed_at, "2026-11-19T14:00")
        self.assertEqual(r.dominant, "PM2.5")
        self.assertEqual(len(r.forecast), 24)
        self.assertEqual(r.forecast[0][0], "2026-11-19T14:00")  # starts at "now"
        self.assertEqual(r.to_dict()["forecast"][0], {"time": "2026-11-19T14:00", "aqi": 120})

    def test_missing_hourly_and_extras_is_ok(self):
        def f(url):
            if "geocoding" in url:
                return {"results": [{"name": "X", "latitude": 1, "longitude": 2}]}
            return {"current": {"us_aqi": 50}}
        r = fetch_live("X", f)
        self.assertEqual((r.aqi, r.forecast, r.ozone, r.observed_at), (50, (), None, None))

    def test_errors(self):
        with self.assertRaises(LocationNotFound):
            fetch_live("zzz", open_meteo())
        with self.assertRaises(DataSourceError):
            fetch_live_coords(95, 0, fetcher=open_meteo())
        self.assertIsNone(valid_coords("a", 1))
        self.assertIsNone(valid_coords(0, 181))

    def test_requests_hourly_and_timezone(self):
        calls = []
        fetch_live("Delhi", open_meteo(calls))
        air = [c for c in calls if "air-quality" in c][0]
        for needle in ("hourly=us_aqi", "timezone=auto", "ozone", "nitrogen_dioxide"):
            self.assertIn(needle, air)

    def test_gas_driven_dominant(self):
        def f(url):
            if "geocoding" in url:
                return {"results": [{"name": "X", "latitude": 1, "longitude": 2}]}
            return {"current": {"us_aqi": 150, "pm2_5": 5.0, "pm10": 20.0, "ozone": 200}}
        self.assertEqual(fetch_live("X", f).dominant, "Ozone / gases")


class CacheTests(unittest.TestCase):
    def setUp(self):
        clear_caches()
        self.calls = []
        self.fetcher = open_meteo(self.calls)

    def tearDown(self):
        clear_caches()

    def n_air(self):
        return len([c for c in self.calls if "air-quality" in c])

    def test_ttl_and_key_normalisation(self):
        fetch_live("Delhi", self.fetcher)
        self.assertTrue(fetch_live("  delhi ", self.fetcher).cached)
        self.assertEqual(self.n_air(), 1)
        with mock.patch("time.monotonic", return_value=time.monotonic() + 601):
            self.assertFalse(fetch_live("Delhi", self.fetcher).cached)
        self.assertEqual(self.n_air(), 2)
        self.assertEqual(len([c for c in self.calls if "geocoding" in c]), 1)  # coords cached

    def test_serves_stale_on_upstream_error(self):
        fetch_live("Delhi", self.fetcher)
        down = open_meteo(fail_air=True)
        with mock.patch("time.monotonic", return_value=time.monotonic() + 1200):
            r = fetch_live("Delhi", down)
            self.assertEqual((r.aqi, r.cached), (168, True))
        self.assertEqual(cache_stats()["stale_served"], 1)
        with mock.patch("time.monotonic", return_value=time.monotonic() + 5 * 3600):
            with self.assertRaises(DataSourceError):  # too old to serve
                fetch_live("Delhi", down)

    def test_not_found_not_masked(self):
        with self.assertRaises(LocationNotFound):
            fetch_live("zzz", self.fetcher)

    def test_coords_label_and_cache(self):
        r = fetch_live_coords(28.6, 77.2, fetcher=self.fetcher)
        self.assertEqual(r.place, "Your location")
        self.assertTrue(fetch_live_coords(28.601, 77.204, fetcher=self.fetcher).cached)  # ~1 km bucket
        self.assertEqual(self.n_air(), 1)


class InsightTests(unittest.TestCase):
    def rows(self, start="2026-11-19T14:00", hours=24, **kw):
        s = hourly_series(start, hours, **kw)
        return tuple(zip(s["time"], s["us_aqi"]))

    def test_best_window_in_waking_hours(self):
        w = best_window(self.rows())
        self.assertEqual((fmt_window(w), w.avg_aqi), ("4 PM–6 PM", 70))

    def test_never_suggests_night(self):
        rows = self.rows(shape=lambda h: 20 if h < 5 or h >= 23 else 150)
        w = best_window(rows)
        self.assertGreaterEqual(w.avg_aqi, 150)  # the 20-AQI night hours are never offered
        self.assertTrue(5 <= int(w.start[11:13]) < 22)

    def test_trend(self):
        self.assertEqual(trend(self.rows()), "improving")
        self.assertEqual(trend(self.rows(shape=lambda h: 100)), "steady")
        self.assertEqual(trend((("2026-11-19T14:00", 50), ("2026-11-19T15:00", 90), ("2026-11-19T16:00", 95))), "worsening")
        self.assertIsNone(trend(()))

    def test_fmt_hour(self):
        self.assertEqual([fmt_hour(f"2026-11-19T{h:02d}:00") for h in (0, 9, 12, 18)],
                         ["12 AM", "9 AM", "12 PM", "6 PM"])


class AssistantLiveTests(unittest.TestCase):
    def setUp(self):
        clear_caches()

    def test_live_advice_includes_forecast_and_freshness(self):
        fetch = lambda c: fetch_live(c, open_meteo())
        s = respond("air quality in Delhi", None, live_fetch=fetch, use_llm=False).state
        s = respond("none", s, live_fetch=fetch, use_llm=False).state
        r = respond("going for a walk", s, live_fetch=fetch, use_llm=False)
        self.assertIn("live data, updated 2 PM local time", r.text)
        self.assertIn("Best time to go out today: 4 PM–6 PM", r.text)
        self.assertIn("improving", r.text)
        self.assertIsNotNone(r.live)

    def test_stricter_advice_is_explained(self):
        fetch = lambda c: fetch_live(c, open_meteo())
        r = respond("AQI is 130 in Pune, I have asthma and want to run", None, live_fetch=fetch, use_llm=False)
        self.assertIn("stricter because of a lung condition", r.text)
        self.assertEqual(r.state.location, "Pune")  # location kept even when a number is given

    def test_refreshes_each_turn_while_live(self):
        calls = []
        fetch = lambda c: (calls.append(c), fetch_live(c, open_meteo()))[1]
        s = respond("air in Delhi", None, live_fetch=fetch, use_llm=False).state
        respond("none", s, live_fetch=fetch, use_llm=False)
        self.assertEqual(len(calls), 2)

    def test_user_numbers_stop_live_refresh(self):
        calls = []
        fetch = lambda c: (calls.append(c), fetch_live(c, open_meteo()))[1]
        s = respond("air in Delhi", None, live_fetch=fetch, use_llm=False).state
        s = respond("actually AQI is 40", s, live_fetch=fetch, use_llm=False).state
        respond("walk", s, live_fetch=fetch, use_llm=False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(s.source, "user")

    def test_transient_failure_keeps_last_reading(self):
        ok = lambda c: fetch_live(c, open_meteo())
        s = respond("air in Delhi", None, live_fetch=ok, use_llm=False).state

        def boom(c):
            raise DataSourceError("down")
        r = respond("none", s, live_fetch=boom, use_llm=False)
        self.assertFalse(r.state.fetch_failed)
        self.assertEqual(r.state.aqi, 168)

    def test_share_location_flow(self):
        coords_fetch = lambda la, lo: fetch_live_coords(la, lo, fetcher=open_meteo())
        r = respond("what is the air at my location?", None, coords=(28.6, 77.2),
                    coords_fetch=coords_fetch, live_fetch=None, use_llm=False)
        self.assertEqual((r.state.location, r.state.lat, r.state.lon), ("Your location", 28.6, 77.2))
        self.assertEqual(r.state.aqi, 168)
        # typing a city afterwards drops the stored coordinates
        r2 = respond("what about in Pune", r.state, coords_fetch=coords_fetch,
                     live_fetch=lambda c: fetch_live(c, open_meteo()), use_llm=False)
        self.assertEqual((r2.state.location, r2.state.lat), ("Pune", None))

    def test_state_validates_coordinates(self):
        ctx = UserContext.from_dict({"lat": 999, "lon": 5})
        self.assertEqual((ctx.lat, ctx.lon), (None, None))
        ctx = UserContext.from_dict({"lat": 12.5, "lon": 77.5})
        self.assertEqual(ctx.to_dict()["lat"], 12.5)


if __name__ == "__main__":
    unittest.main()

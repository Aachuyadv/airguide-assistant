"""Realistic Open-Meteo payloads for offline tests."""
from datetime import datetime, timedelta


def hourly_series(start="2026-11-19T00:00", hours=48, shape=None):
    t0 = datetime.strptime(start, "%Y-%m-%dT%H:%M")
    times = [(t0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(hours)]
    shape = shape or (lambda h: 170 if 7 <= h <= 11 else 120 if 12 <= h <= 15 else 70 if 16 <= h <= 20 else 140)
    return {"time": times, "us_aqi": [shape(datetime.strptime(t, "%Y-%m-%dT%H:%M").hour) for t in times]}


def open_meteo(calls=None, fail_air=False):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        if "geocoding" in url:
            if "zzz" in url:
                return {}
            return {"results": [{"name": "Delhi", "country": "India", "latitude": 28.65, "longitude": 77.23}]}
        if fail_air:
            from airguide.data_source import DataSourceError
            raise DataSourceError("upstream down")
        return {
            "timezone": "Asia/Kolkata",
            "current": {"time": "2026-11-19T14:00", "interval": 3600, "us_aqi": 168,
                        "pm10": 140.0, "pm2_5": 80.0, "ozone": 45.0,
                        "nitrogen_dioxide": 38.5, "sulphur_dioxide": 12.0, "carbon_monoxide": 640.0},
            "hourly": hourly_series(),
        }
    return fetch

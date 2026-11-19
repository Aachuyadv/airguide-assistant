"""Optional live air-quality lookup via Open-Meteo (free, no API key)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
TIMEOUT_S = 8
MAX_BYTES = 200_000

Fetcher = Callable[[str], dict]


@dataclass(frozen=True)
class LiveReading:
    place: str
    aqi: int | None
    pm25: float | None
    pm10: float | None


class DataSourceError(Exception):
    pass


def http_get_json(url: str) -> dict:
    if not url.startswith("https://"):
        raise DataSourceError("only https is allowed")
    req = urllib.request.Request(url, headers={"User-Agent": "airguide-assistant/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
            raw = resp.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DataSourceError(str(exc)) from exc
    if len(raw) > MAX_BYTES:
        raise DataSourceError("response too large")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise DataSourceError("invalid JSON") from exc
    if not isinstance(data, dict):
        raise DataSourceError("unexpected response shape")
    return data


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def fetch_live(city: str, fetcher: Fetcher = http_get_json) -> LiveReading:
    """Geocode `city` then fetch current readings. Raises DataSourceError."""
    geo = fetcher(
        f"{GEOCODE_URL}?{urllib.parse.urlencode({'name': city, 'count': 1})}"
    )
    results = geo.get("results")
    if not isinstance(results, list) or not results:
        raise DataSourceError("location not found")
    top = results[0]
    lat, lon = _num(top.get("latitude")), _num(top.get("longitude"))
    if lat is None or lon is None:
        raise DataSourceError("bad coordinates")
    place = ", ".join(
        p for p in (top.get("name"), top.get("country")) if isinstance(p, str)
    ) or city

    query = urllib.parse.urlencode(
        {"latitude": lat, "longitude": lon, "current": "pm10,pm2_5,us_aqi"}
    )
    air = fetcher(f"{AIR_URL}?{query}")
    current = air.get("current")
    if not isinstance(current, dict):
        raise DataSourceError("no current data")
    aqi = _num(current.get("us_aqi"))
    pm25, pm10 = _num(current.get("pm2_5")), _num(current.get("pm10"))
    if aqi is None and pm25 is None and pm10 is None:
        raise DataSourceError("no usable readings")
    return LiveReading(
        place=place,
        aqi=None if aqi is None else max(0, min(500, round(aqi))),
        pm25=pm25,
        pm10=pm10,
    )

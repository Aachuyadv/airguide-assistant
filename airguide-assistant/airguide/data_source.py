"""Real-time air-quality data via Open-Meteo (free, no API key).

Zero external dependencies (pure standard library).
Features:
- Current pollutants (PM2.5, PM10, O3, NO2, SO2, CO) plus a 24-hour hourly AQI forecast.
- Permanent geocoding cache (city coordinates never change).
- 10-minute TTL reading cache; on upstream failure the last reading (up to 3 h old)
  is served instead of an error.
- Outbound request bounds (8 s timeout, one retry, 200 KB payload cap, HTTPS only).
- Lookup by city name or by browser-shared coordinates.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from typing import Any, Callable

from .aqi import PM10_BREAKPOINTS, PM25_BREAKPOINTS, sub_index

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
PROVIDER = "Open-Meteo Air Quality (CAMS)"
TIMEOUT_S = 8.0
MAX_BYTES = 200_000
RETRIES = 1
DEFAULT_CACHE_TTL_S = 600.0  # 10 minutes for atmospheric readings
STALE_OK_S = 3 * 3600.0      # serve a cached reading this old if the API is down
CACHE_MAX = 256
FORECAST_HOURS = 24
CURRENT_FIELDS = "us_aqi,pm10,pm2_5,ozone,nitrogen_dioxide,sulphur_dioxide,carbon_monoxide"

Fetcher = Callable[[str], dict[str, Any]]


class DataSourceError(Exception):
    """Raised when an external data fetch or parsing fails."""


class LocationNotFound(DataSourceError):
    """Raised when geocoding finds no matching place."""


@dataclass(frozen=True)
class LiveReading:
    """A validated atmospheric observation with optional forecast."""

    place: str
    aqi: int | None
    pm25: float | None
    pm10: float | None
    cached: bool = False
    observed_at: str | None = None  # local time at the location, "YYYY-MM-DDTHH:MM"
    ozone: float | None = None
    no2: float | None = None
    so2: float | None = None
    co: float | None = None
    dominant: str | None = None
    lat: float | None = None
    lon: float | None = None
    forecast: tuple[tuple[str, int], ...] = ()  # (local ISO hour, US AQI), starting now
    provider: str = PROVIDER

    def to_dict(self) -> dict[str, Any]:
        return {
            "place": self.place, "aqi": self.aqi, "pm25": self.pm25, "pm10": self.pm10,
            "ozone": self.ozone, "no2": self.no2, "so2": self.so2, "co": self.co,
            "dominant": self.dominant, "observed_at": self.observed_at,
            "lat": self.lat, "lon": self.lon, "cached": self.cached,
            "forecast": [{"time": t, "aqi": a} for t, a in self.forecast],
            "provider": self.provider,
        }


# In-memory caches (thread-safety: single-key dict ops are atomic under the GIL)
_GEO_CACHE: dict[str, tuple[float, float, str]] = {}
_READING_CACHE: dict[str, tuple[float, LiveReading]] = {}
_STATS = {"hits": 0, "misses": 0, "stale_served": 0}


def clear_caches() -> None:
    """Flush all in-memory caches (useful for testing)."""
    _GEO_CACHE.clear()
    _READING_CACHE.clear()
    for k in _STATS:
        _STATS[k] = 0


def cache_stats() -> dict[str, int]:
    return {"readings": len(_READING_CACHE), "places": len(_GEO_CACHE), **_STATS}


def http_get_json(url: str) -> dict[str, Any]:
    """Secure, bounded HTTPS GET returning parsed JSON (one retry on network faults)."""
    if not url.startswith("https://"):
        raise DataSourceError("only https is allowed")
    req = urllib.request.Request(
        url, headers={"User-Agent": "airguide-assistant/2.0", "Accept": "application/json"}
    )
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
                raw = resp.read(MAX_BYTES + 1)
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < RETRIES:
                time.sleep(0.4)
    else:
        raise DataSourceError(str(last)) from last
    if len(raw) > MAX_BYTES:
        raise DataSourceError("response too large")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise DataSourceError("invalid JSON") from exc
    if not isinstance(data, dict):
        raise DataSourceError("unexpected response shape")
    return data


# ------------------------------------------------------------------------ parsing
def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value:  # NaN
        return None
    return float(value)


def _clamp_aqi(value: float) -> int:
    return max(0, min(500, round(value)))


def _dominant(aqi: int | None, pm25: float | None, pm10: float | None) -> str | None:
    subs = {}
    if pm25 is not None:
        subs["PM2.5"] = sub_index(pm25, PM25_BREAKPOINTS, 1)
    if pm10 is not None:
        subs["PM10"] = sub_index(pm10, PM10_BREAKPOINTS, 0)
    if aqi is None or not subs:
        return None
    name, value = max(subs.items(), key=lambda kv: kv[1])
    return name if value >= aqi - 5 else "Ozone / gases"


def _forecast(air: dict, observed_at: str | None) -> tuple[tuple[str, int], ...]:
    hourly = air.get("hourly")
    if not isinstance(hourly, dict):
        return ()
    times, values = hourly.get("time"), hourly.get("us_aqi")
    if not isinstance(times, list) or not isinstance(values, list):
        return ()
    start = observed_at[:13] if observed_at else None
    rows: list[tuple[str, int]] = []
    for t, v in zip(times, values):
        n = _num(v)
        if not isinstance(t, str) or n is None:
            continue
        if start and t[:13] < start:  # ISO strings sort chronologically
            continue
        rows.append((t[:16], _clamp_aqi(n)))
        if len(rows) >= FORECAST_HOURS:
            break
    return tuple(rows)


def _parse_air(place: str, lat: float, lon: float, air: dict) -> LiveReading:
    current = air.get("current")
    if not isinstance(current, dict):
        raise DataSourceError("no current data")
    aqi = _num(current.get("us_aqi"))
    pm25, pm10 = _num(current.get("pm2_5")), _num(current.get("pm10"))
    if aqi is None and pm25 is None and pm10 is None:
        raise DataSourceError("no usable readings")
    aqi_i = None if aqi is None else _clamp_aqi(aqi)
    t = current.get("time")
    observed = t[:16] if isinstance(t, str) else None
    return LiveReading(
        place=place,
        aqi=aqi_i,
        pm25=pm25,
        pm10=pm10,
        observed_at=observed,
        ozone=_num(current.get("ozone")),
        no2=_num(current.get("nitrogen_dioxide")),
        so2=_num(current.get("sulphur_dioxide")),
        co=_num(current.get("carbon_monoxide")),
        dominant=_dominant(aqi_i, pm25, pm10),
        lat=lat,
        lon=lon,
        forecast=_forecast(air, observed),
    )


def _air_url(lat: float, lon: float) -> str:
    return AIR_URL + "?" + urllib.parse.urlencode(
        {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "current": CURRENT_FIELDS,
            "hourly": "us_aqi",
            "forecast_days": 2,
            "timezone": "auto",
        }
    )


def valid_coords(lat: Any, lon: Any) -> tuple[float, float] | None:
    la, lo = _num(lat), _num(lon)
    if la is None or lo is None or not (-90 <= la <= 90) or not (-180 <= lo <= 180):
        return None
    return la, lo


# ---------------------------------------------------------------- cached fetching
def _cached_reading(
    key: str, ttl: float, use_cache: bool, loader: Callable[[], LiveReading]
) -> LiveReading:
    now = time.monotonic()
    item = _READING_CACHE.get(key) if use_cache else None
    if item and now - item[0] < ttl:
        _STATS["hits"] += 1
        return replace(item[1], cached=True)
    _STATS["misses"] += 1
    try:
        reading = loader()
    except LocationNotFound:
        raise
    except DataSourceError:
        if item and now - item[0] <= STALE_OK_S:  # upstream down: serve last good data
            _STATS["stale_served"] += 1
            return replace(item[1], cached=True)
        raise
    if use_cache:
        if len(_READING_CACHE) >= CACHE_MAX:
            del _READING_CACHE[min(_READING_CACHE, key=lambda k: _READING_CACHE[k][0])]
        _READING_CACHE[key] = (now, reading)
    return reading


def fetch_live(
    city: str,
    fetcher: Fetcher = http_get_json,
    ttl: float = DEFAULT_CACHE_TTL_S,
    use_cache: bool = True,
) -> LiveReading:
    """Geocode `city` then fetch current readings and the hourly forecast.

    Raises DataSourceError (LocationNotFound when the place is unknown).
    """
    clean_city = city.strip()
    cache_key = "c:" + " ".join(clean_city.lower().split())

    def load() -> LiveReading:
        geo_key = cache_key
        if use_cache and geo_key in _GEO_CACHE:
            lat, lon, place = _GEO_CACHE[geo_key]
        else:
            geo = fetcher(
                f"{GEOCODE_URL}?{urllib.parse.urlencode({'name': clean_city, 'count': 1, 'language': 'en'})}"
            )
            results = geo.get("results")
            if not isinstance(results, list) or not results:
                raise LocationNotFound("location not found")
            top = results[0]
            coords = valid_coords(top.get("latitude"), top.get("longitude"))
            if coords is None:
                raise DataSourceError("bad coordinates")
            lat, lon = coords
            place = ", ".join(
                p for p in (top.get("name"), top.get("country")) if isinstance(p, str)
            ) or clean_city
            if use_cache:
                _GEO_CACHE[geo_key] = (lat, lon, place)
        return _parse_air(place, lat, lon, fetcher(_air_url(lat, lon)))

    return _cached_reading(cache_key, ttl, use_cache, load)


def fetch_live_coords(
    lat: float,
    lon: float,
    label: str = "Your location",
    fetcher: Fetcher = http_get_json,
    ttl: float = DEFAULT_CACHE_TTL_S,
    use_cache: bool = True,
) -> LiveReading:
    """Fetch readings for exact coordinates (e.g. shared by the browser)."""
    coords = valid_coords(lat, lon)
    if coords is None:
        raise DataSourceError("bad coordinates")
    key = f"g:{round(coords[0], 2)}:{round(coords[1], 2)}"
    reading = _cached_reading(
        key, ttl, use_cache, lambda: _parse_air(label, coords[0], coords[1], fetcher(_air_url(*coords)))
    )
    return reading if reading.place == label else replace(reading, place=label)

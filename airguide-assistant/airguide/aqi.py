"""Deterministic AQI maths (US EPA scale, 2024 PM2.5 breakpoints).

The language model never calculates anything: every figure it quotes comes
from this module.
"""
from __future__ import annotations

import math

# (low, high, label)
CATEGORIES: tuple[tuple[int, int, str], ...] = (
    (0, 50, "Good"),
    (51, 100, "Moderate"),
    (101, 150, "Unhealthy for Sensitive Groups"),
    (151, 200, "Unhealthy"),
    (201, 300, "Very Unhealthy"),
    (301, 500, "Hazardous"),
)

# (conc_low, conc_high, aqi_low, aqi_high)
PM25_BREAKPOINTS = (
    (0.0, 9.0, 0, 50),
    (9.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 125.4, 151, 200),
    (125.5, 225.4, 201, 300),
    (225.5, 325.4, 301, 500),
)
PM10_BREAKPOINTS = (
    (0, 54, 0, 50),
    (55, 154, 51, 100),
    (155, 254, 101, 150),
    (255, 354, 151, 200),
    (355, 424, 201, 300),
    (425, 604, 301, 500),
)

MAX_AQI = 500


def _truncate(value: float, decimals: int) -> float:
    factor = 10**decimals
    return math.floor(value * factor + 1e-9) / factor


def sub_index(concentration: float, table, decimals: int) -> int:
    """Convert a pollutant concentration (ug/m3) to an AQI sub-index."""
    if concentration is None or math.isnan(concentration) or concentration < 0:
        raise ValueError("concentration must be a non-negative number")
    conc = _truncate(concentration, decimals)
    if conc > table[-1][1]:
        return MAX_AQI
    for c_lo, c_hi, i_lo, i_hi in table:
        if c_lo <= conc <= c_hi:
            return round((i_hi - i_lo) / (c_hi - c_lo) * (conc - c_lo) + i_lo)
    # Gap between truncated breakpoints (e.g. 9.05 -> 9.0 handled above); fall back.
    for c_lo, c_hi, i_lo, i_hi in table:
        if conc < c_lo:
            return i_lo
    return MAX_AQI


def overall_aqi(pm25: float | None = None, pm10: float | None = None) -> int | None:
    """AQI is the worst (max) sub-index of the pollutants provided."""
    values = []
    if pm25 is not None:
        values.append(sub_index(pm25, PM25_BREAKPOINTS, 1))
    if pm10 is not None:
        values.append(sub_index(pm10, PM10_BREAKPOINTS, 0))
    return max(values) if values else None


def level_index(aqi: int) -> int:
    """0 (Good) .. 5 (Hazardous)."""
    if aqi < 0:
        raise ValueError("AQI cannot be negative")
    for idx, (_, high, _) in enumerate(CATEGORIES):
        if aqi <= high:
            return idx
    return len(CATEGORIES) - 1


def category(aqi: int) -> str:
    return CATEGORIES[level_index(aqi)][2]

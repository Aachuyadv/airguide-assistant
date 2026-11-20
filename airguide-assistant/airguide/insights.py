"""Deterministic insights derived from the hourly forecast (no LLM involved)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

DAY_START_H, DAY_END_H = 5, 22  # only suggest sensible waking hours


@dataclass(frozen=True)
class Window:
    start: str  # local ISO hour
    end: str    # local ISO hour (exclusive)
    avg_aqi: int


def _dt(iso: str) -> datetime:
    return datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")


def fmt_hour(iso: str) -> str:
    d = _dt(iso)
    h = d.hour % 12 or 12
    return f"{h} {'AM' if d.hour < 12 else 'PM'}"


def fmt_window(w: Window) -> str:
    return f"{fmt_hour(w.start)}–{fmt_hour(w.end)}"


def best_window(forecast, width: int = 2, horizon: int = 18) -> Window | None:
    """Lowest-average `width`-hour block in waking hours within the horizon."""
    rows = list(forecast)[:horizon]
    best: Window | None = None
    for i in range(len(rows) - width + 1):
        block = rows[i : i + width]
        hours = [_dt(t).hour for t, _ in block]
        if not all(DAY_START_H <= h < DAY_END_H for h in hours):
            continue
        # must be consecutive hours
        if any(_dt(block[j + 1][0]) - _dt(block[j][0]) != timedelta(hours=1) for j in range(width - 1)):
            continue
        avg = round(sum(a for _, a in block) / width)
        if best is None or avg < best.avg_aqi:
            end = (_dt(block[-1][0]) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
            best = Window(block[0][0], end, avg)
    return best


def trend(forecast, hours: int = 6, threshold: int = 10) -> str | None:
    """'improving' | 'worsening' | 'steady' over the next `hours`, or None."""
    rows = list(forecast)
    if len(rows) < 3:
        return None
    ahead = [a for _, a in rows[1 : hours + 1]]
    delta = sum(ahead) / len(ahead) - rows[0][1]
    if delta <= -threshold:
        return "improving"
    if delta >= threshold:
        return "worsening"
    return "steady"

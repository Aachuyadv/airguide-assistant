"""User context (the assistant's dynamic state) with strict validation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .aqi import overall_aqi

UNKNOWN = "UNKNOWN"
SENSITIVE_GROUPS = ("lung", "heart", "child", "elderly", "pregnant")
# Activity -> exertion level (0 = indoors, 1 = light, 2 = vigorous)
ACTIVITIES = {"indoor": 0, "walk": 1, "commute": 1, "exercise": 2}
ASK_FIELDS = ("air_reading", "sensitivity", "activity")

_LOCATION_BAD_CHARS = re.compile(r"[^A-Za-z0-9 ,.'\-]")
MAX_LOCATION_LEN = 60


def sanitize_location(value: Any) -> str | None:
    """Keep only characters a place name needs (also blocks prompt injection)."""
    if not isinstance(value, str):
        return None
    cleaned = _LOCATION_BAD_CHARS.sub("", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()[:MAX_LOCATION_LEN]
    return cleaned or None


def _bounded_float(value: Any, low: float, high: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or not (low <= number <= high):  # NaN or out of range
        return None
    return number


@dataclass
class UserContext:
    location: str | None = None
    aqi_reported: int | None = None  # stated by user or from live feed
    pm25: float | None = None
    pm10: float | None = None
    source: str | None = None  # "live" | "user"
    sensitivity: tuple[str, ...] | None = None  # None = unknown, () = none
    activity: str | None = None
    awaiting: str | None = None  # which field we last asked about
    stage: str = "gathering"
    fetch_failed: bool = False

    @property
    def aqi(self) -> int | None:
        """Reported AQI wins; otherwise computed from PM readings."""
        if self.aqi_reported is not None:
            return self.aqi_reported
        return overall_aqi(self.pm25, self.pm10)

    # ---- serialisation (state round-trips through the browser, so validate) ----
    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "aqi_reported": self.aqi_reported,
            "pm25": self.pm25,
            "pm10": self.pm10,
            "source": self.source,
            "sensitivity": None if self.sensitivity is None else list(self.sensitivity),
            "activity": self.activity,
            "awaiting": self.awaiting,
            "stage": self.stage,
            "fetch_failed": self.fetch_failed,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "UserContext":
        if not isinstance(data, dict):
            return cls()
        aqi = _bounded_float(data.get("aqi_reported"), 0, 500)
        sens_raw = data.get("sensitivity")
        sensitivity: tuple[str, ...] | None = None
        if isinstance(sens_raw, list):
            sensitivity = tuple(s for s in SENSITIVE_GROUPS if s in sens_raw)
        activity = data.get("activity")
        awaiting = data.get("awaiting")
        source = data.get("source")
        return cls(
            location=sanitize_location(data.get("location")),
            aqi_reported=None if aqi is None else int(aqi),
            pm25=_bounded_float(data.get("pm25"), 0, 1000),
            pm10=_bounded_float(data.get("pm10"), 0, 2000),
            source=source if source in ("live", "user") else None,
            sensitivity=sensitivity,
            activity=activity if activity in ACTIVITIES else None,
            awaiting=awaiting if awaiting in ASK_FIELDS else None,
            stage="advising" if data.get("stage") == "advising" else "gathering",
            fetch_failed=bool(data.get("fetch_failed")),
        )

    # ---- prompt rendering helpers ----
    def prompt_fields(self) -> dict[str, str]:
        aqi = self.aqi
        sens = (
            UNKNOWN
            if self.sensitivity is None
            else (", ".join(self.sensitivity) or "none")
        )
        return {
            "location": self.location or UNKNOWN,
            "aqi": UNKNOWN if aqi is None else str(aqi),
            "pm25": UNKNOWN if self.pm25 is None else f"{self.pm25:g}",
            "pm10": UNKNOWN if self.pm10 is None else f"{self.pm10:g}",
            "sensitivity": sens,
            "activity": self.activity or UNKNOWN,
            "stage": self.stage,
        }

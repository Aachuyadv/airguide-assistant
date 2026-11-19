"""Optional Gen-AI phrasing layer (Anthropic Messages API).

Disabled unless ANTHROPIC_API_KEY is set. Any failure returns None so the
caller falls back to the deterministic reply: the assistant works offline.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_WORDS = 150


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def generate(system: str, user_message: str, timeout: float = 15.0) -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    body = json.dumps(
        {
            "model": os.environ.get("AIRGUIDE_MODEL", DEFAULT_MODEL),
            "max_tokens": 300,
            "system": system,
            "messages": [{"role": "user", "content": user_message[:500]}],
        }
    ).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read(200_000))
        text = "".join(
            block.get("text", "")
            for block in payload.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        return None
    if not text or len(text.split()) > MAX_WORDS:
        return None
    return text

"""Zero-dependency web server: static UI + JSON chat API."""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .assistant import respond
from .context import UserContext

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/style.css": ("style.css", "text/css; charset=utf-8"),
}
MAX_BODY = 8192
RATE_LIMIT = 30  # requests per window per client
RATE_WINDOW_S = 60

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class RateLimiter:
    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW_S):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[client]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


def build_handler(live_fetch=None, use_llm: bool = True, limiter: RateLimiter | None = None):
    limiter = limiter or RateLimiter()
    kwargs = {} if live_fetch is None else {"live_fetch": live_fetch}

    class Handler(BaseHTTPRequestHandler):
        server_version = "AirGuide"
        sys_version = ""

        def log_message(self, fmt, *args):  # keep logs free of user content
            pass

        def _send(self, status: int, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict):
            self._send(status, json.dumps(payload).encode(), "application/json")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                return self._json(200, {"status": "ok"})
            entry = STATIC.get(path)
            if not entry:
                return self._json(404, {"error": "not found"})
            name, ctype = entry
            self._send(200, (WEB_DIR / name).read_bytes(), ctype)

        def do_POST(self):
            if self.path != "/api/chat":
                return self._json(404, {"error": "not found"})
            if not limiter.allow(self.client_address[0]):
                return self._json(429, {"error": "Too many requests. Please wait a moment."})
            if "application/json" not in self.headers.get("Content-Type", ""):
                return self._json(415, {"error": "JSON required"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._json(400, {"error": "bad length"})
            if length <= 0 or length > MAX_BODY:
                return self._json(413, {"error": "request too large"})
            try:
                data = json.loads(self.rfile.read(length))
                message = data["message"]
                if not isinstance(message, str):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"error": "expected {message: string, state: object}"})

            state = UserContext.from_dict(data.get("state"))
            reply = respond(message, state, use_llm=use_llm, **kwargs)
            d = reply.directive
            aqi = reply.state.aqi
            self._json(
                200,
                {
                    "reply": reply.text,
                    "state": reply.state.to_dict(),
                    "summary": {
                        "aqi": aqi,
                        "verdict": d.verdict,
                        "location": reply.state.location,
                    },
                },
            )

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    httpd = ThreadingHTTPServer((host, port), build_handler())
    print(f"AirGuide running at http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    serve(os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "8000")))

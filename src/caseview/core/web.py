"""Phone access: a small web page + JSON API on the local network.

Security:
- off by default; when on, only private-network addresses (home Wi-Fi/LAN) are served
- every API call needs the 6-digit PIN (header X-CaseView-PIN), compared in constant time
- 5 wrong PINs from one address lock it out for 60 s
Stdlib only (http.server), one small thread per request; nothing runs while idle.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any, Protocol

log = logging.getLogger(__name__)

MAX_BODY = 4096
MAX_FAILS, LOCKOUT_S = 5, 60.0


class Controller(Protocol):
    def web_state(self) -> dict[str, Any]: ...
    def web_set_rgb(self, changes: dict[str, Any]) -> None: ...
    def web_apply_profile(self, name: str) -> None: ...
    def web_set_slot(self, slot: str, sensor: str) -> None: ...
    def web_set_pages(self, pages: list[dict[str, Any]], page_seconds: float) -> None: ...
    def web_lock_page(self, name: str) -> None: ...


def new_pin() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def lan_address() -> str:
    """This PC's address on the local network (no packets are sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return str(s.getsockname()[0])
        except OSError:
            return "127.0.0.1"


def _allowed(client_ip: str) -> bool:
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


class WebServer:
    def __init__(self, controller: Controller, port: int, pin: str) -> None:
        self.controller = controller
        self.port = port
        self.pin = pin
        self._fails: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._page = resources.files("caseview.web").joinpath("index.html").read_bytes()

    @property
    def url(self) -> str:
        return f"http://{lan_address()}:{self.port}"

    def start(self) -> None:
        handler = _make_handler(self)
        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="web", daemon=True)
        self._thread.start()
        log.info("phone access on %s", self.url)

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    # -- auth -------------------------------------------------------------

    def check_pin(self, ip: str, given: str) -> str:
        """'ok', 'bad' or 'locked'."""
        now = time.monotonic()
        with self._lock:
            fails, since = self._fails.get(ip, (0, now))
            if fails >= MAX_FAILS and now - since < LOCKOUT_S:
                return "locked"
            if hmac.compare_digest(given.encode(), self.pin.encode()):
                self._fails.pop(ip, None)
                return "ok"
            if now - since >= LOCKOUT_S:
                fails, since = 0, now
            self._fails[ip] = (fails + 1, since)
            return "bad"


def _make_handler(server: WebServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "CaseView"

        def log_message(self, format: str, *args: Any) -> None:  # quiet by default
            log.debug("web %s - %s", self.client_address[0], format % args)

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data: Any) -> None:
            self._send(code, json.dumps(data).encode())

        def _guard(self) -> bool:
            ip = self.client_address[0]
            if not _allowed(ip):
                self._json(403, {"error": "only devices on your home network can connect"})
                return False
            if not self.path.startswith("/api/"):
                return True
            result = server.check_pin(ip, self.headers.get("X-CaseView-PIN", ""))
            if result == "ok":
                return True
            if result == "locked":
                self._json(429, {"error": "too many wrong PINs, wait a minute"})
            else:
                self._json(401, {"error": "wrong PIN"})
            return False

        def do_GET(self) -> None:
            if not self._guard():
                return
            if self.path in ("/", "/index.html"):
                self._send(200, server._page, "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._json(200, server.controller.web_state())
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._guard():
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > MAX_BODY:
                self._json(413, {"error": "request too large"})
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("expected an object")
                route: dict[str, Callable[[], None]] = {
                    "/api/rgb": lambda: server.controller.web_set_rgb(body),
                    "/api/profile": lambda: server.controller.web_apply_profile(str(body["name"])),
                    "/api/slot": lambda: server.controller.web_set_slot(
                        str(body["slot"]), str(body["sensor"])
                    ),
                    "/api/lock": lambda: server.controller.web_lock_page(str(body.get("name", ""))),
                    "/api/pages": lambda: server.controller.web_set_pages(
                        list(body["pages"]), float(body.get("page_seconds", 5))
                    ),
                }
                action = route.get(self.path)
                if action is None:
                    self._json(404, {"error": "not found"})
                    return
                action()
            except (ValueError, KeyError, TypeError) as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, server.controller.web_state())

    return Handler

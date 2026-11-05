import asyncio
import json
import re
import sys
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from casework.messages import explain
from casework.model import Refusal

STATIC = Path(__file__).parent / "static"
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/cockpit.css": ("cockpit.css", "text/css; charset=utf-8"),
    "/cockpit.js": ("cockpit.js", "text/javascript; charset=utf-8"),
}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
EVAL_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
STATUS = {
    "unauthenticated": 401, "forbidden": 403, "case_not_accessible": 404, "unknown_grant": 404, "not_found": 404,
}
MAX_BODY = 4096
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
RUN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class CockpitServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, service, loop, host: str = "127.0.0.1", port: int = 0):
        self.service = service
        self.loop = loop
        super().__init__((host, port), _Handler)

    @property
    def allowed_hosts(self) -> set[str]:
        port = self.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    def call(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=60)


class _Handler(BaseHTTPRequestHandler):
    server: CockpitServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def do_GET(self):
        self._serve(self._get)

    def do_POST(self):
        self._serve(self._post)

    def _serve(self, route):
        try:
            if self.headers.get("Host", "") not in self.server.allowed_hosts:
                return self._error("forbidden", 403)
            origin = self.headers.get("Origin")
            if origin and origin.removeprefix("http://") not in self.server.allowed_hosts:
                return self._error("forbidden", 403)
            route()
        except Refusal as refusal:
            self._error(refusal.code, STATUS.get(refusal.code, 400))
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._send_json({"ok": False, "code": "internal", "message": "The cockpit hit an unexpected error.", "next": "Look at the cockpit's terminal for the cause."}, 500)

    def _get(self):
        path = self.path.split("?", 1)[0]
        if path in ASSETS:
            name, kind = ASSETS[path]
            return self._send(200, (STATIC / name).read_bytes(), kind, CSP)
        if path.startswith("/evals/"):
            return self._evaluation(path)
        if not path.startswith("/api/"):
            return self._error("not_found", 404)
        token = self._token()
        service = self.server.service
        parts = path.removeprefix("/api/").split("/")
        if parts == ["me"]:
            return self._send_json(self.server.call(service.me(token)))
        if parts == ["todo"]:
            return self._send_json({"items": self.server.call(service.todo(token))})
        if parts == ["cases"]:
            return self._send_json({"cases": self.server.call(service.cases(token))})
        if len(parts) == 2 and parts[0] == "cases" and NAME.match(parts[1]):
            return self._send_json(self.server.call(service.case(token, parts[1])))
        if parts == ["authority"]:
            return self._send_json(self.server.call(service.authority(token)))
        if parts == ["quality"]:
            return self._send_json(self.server.call(service.quality(token)))
        if parts == ["health"]:
            return self._send_json(self.server.call(service.health(token)))
        self._error("not_found", 404)

    def _post(self):
        path = self.path.split("?", 1)[0]
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self._error("invalid_proposal", 415)
        body = self._body()
        service = self.server.service
        if path == "/api/session":
            token = str(body.get("token", ""))
            me = self.server.call(service.me(token))
            cookie = f"cockpit={token}; HttpOnly; SameSite=Strict; Path=/evals/"
            return self._send_json(me, extra={"Set-Cookie": cookie})
        token = self._token()
        parts = path.removeprefix("/api/").split("/")
        if len(parts) == 3 and parts[0] == "cases" and NAME.match(parts[1]):
            if parts[2] == "review":
                return self._send_json(self.server.call(service.review(token, parts[1], str(body.get("verdict", "")), str(body.get("digest", "")))))
            if parts[2] == "resolve":
                return self._send_json(self.server.call(service.resolve(token, parts[1], str(body.get("kind", "")))))
        if len(parts) == 3 and parts[0] == "authority" and NAME.match(parts[1]) and parts[2] == "revoke":
            return self._send_json(self.server.call(service.revoke(token, parts[1], str(body.get("reason", "")))))
        self._error("not_found", 404)

    def _evaluation(self, path: str):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        if "cockpit" not in cookie:
            raise Refusal("unauthenticated")
        self.server.call(self.server.service.me(cookie["cockpit"].value))
        parts = path.removeprefix("/evals/").split("/")
        evals = self.server.service.evals
        if evals is None or len(parts) != 2 or not RUN.match(parts[0]):
            return self._error("not_found", 404)
        file = evals.dashboard_path(parts[0], parts[1])
        if file is None:
            return self._error("not_found", 404)
        self._send(200, file.read_bytes(), "text/html; charset=utf-8", EVAL_CSP)

    def _token(self) -> str:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer ") or len(header) < 8:
            raise Refusal("unauthenticated")
        return header[7:]

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length < 0 or length > MAX_BODY:
            raise Refusal("invalid_proposal")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except ValueError:
            raise Refusal("invalid_proposal") from None
        if not isinstance(body, dict):
            raise Refusal("invalid_proposal")
        return body

    def _error(self, code: str, status: int):
        self._send_json({"ok": False, **explain(code)}, status)

    def _send_json(self, value, status: int = 200, extra: dict | None = None):
        self._send(status, json.dumps(value, default=str).encode(), "application/json; charset=utf-8", CSP, extra)

    def _send(self, status: int, body: bytes, kind: str, csp: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", csp)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

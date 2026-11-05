import asyncio
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from casework.model import Refusal
from cockpit.quality import EvalRuns
from cockpit.server import CockpitServer


class Stub:
    def __init__(self, evals):
        self.evals = evals
        self.calls = []

    async def me(self, token):
        if token != "good":
            raise Refusal("unauthenticated")
        return {"id": "u", "tenant": "t", "roles": ["operator"]}

    async def todo(self, token):
        await self.me(token)
        return [{"kind": "review", "title": "Review a refund", "detail": "", "case_id": "abc", "since": None, "due": None}]

    async def cases(self, token):
        await self.me(token)
        return []

    async def case(self, token, case_id):
        await self.me(token)
        if case_id == "boom":
            raise RuntimeError("secret internal detail")
        if case_id == "missing":
            raise Refusal("case_not_accessible")
        return {"case_id": case_id}

    async def review(self, token, case_id, verdict, digest):
        await self.me(token)
        self.calls.append(("review", case_id, verdict, digest))
        return {"case_id": case_id}

    async def resolve(self, token, case_id, kind):
        self.calls.append(("resolve", case_id, kind))
        return {"case_id": case_id}

    async def revoke(self, token, grant_id, reason):
        self.calls.append(("revoke", grant_id, reason))
        return {"grants": []}

    async def authority(self, token):
        return {"grants": []}

    async def conversations(self, token):
        await self.me(token)
        return {"configured": True, "conversations": []}

    async def rules(self, token):
        await self.me(token)
        return {"sections": [], "note": "defaults"}

    async def staff_history(self, token):
        await self.me(token)
        return []

    async def quality_run(self, token, run):
        await self.me(token)
        return {"run": run, "cases": []}

    async def replay(self, token, case_id, decision):
        await self.me(token)
        self.calls.append(("replay", case_id, decision))
        return {"reproduced": True}

    async def receipt(self, token, case_id, decision, format):
        await self.me(token)
        self.calls.append(("receipt", case_id, decision, format))
        return b"<svg></svg>", "image/svg+xml", "decision-abc.svg"

    async def grant(self, token, intent, tier, max_amount_minor, days, acknowledged):
        await self.me(token)
        self.calls.append(("grant", intent, tier, max_amount_minor, days, acknowledged))
        return {"grants": []}

    async def activate(self, token, grant_id):
        await self.me(token)
        self.calls.append(("activate", grant_id))
        return {"grants": []}

    async def quality(self, token):
        return {"configured": True, "runs": []}

    async def health(self, token):
        return {"overall": "ok", "checks": []}


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.runs = Path(cls.folder.name)
        (cls.runs / "run-1").mkdir()
        (cls.runs / "run-1" / "dashboard.html").write_text("<html><script>1</script></html>", encoding="utf-8")
        (cls.runs / "run-1" / "report.json").write_text("{}", encoding="utf-8")
        (cls.runs.parent / "outside.html").write_text("secret", encoding="utf-8")
        cls.loop = asyncio.new_event_loop()
        threading.Thread(target=cls.loop.run_forever, daemon=True).start()
        cls.stub = Stub(EvalRuns(cls.runs))
        cls.server = CockpitServer(cls.stub, cls.loop, "127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.loop.call_soon_threadsafe(cls.loop.stop)
        cls.folder.cleanup()

    def request(self, method, path, *, token=None, body=None, headers=None, host=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        sent = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
        if token:
            sent["Authorization"] = f"Bearer {token}"
        payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if payload is not None and "Content-Type" not in sent:
            sent["Content-Type"] = "application/json"
        connection.request(method, path, body=payload, headers=sent)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data

    def json(self, *args, **kwargs):
        response, data = self.request(*args, **kwargs)
        return response, json.loads(data)

    def test_the_page_and_its_assets_are_served_with_strict_headers(self):
        for path, kind in (("/", "text/html"), ("/cockpit.css", "text/css"), ("/cockpit.js", "text/javascript")):
            response, data = self.request("GET", path)
            self.assertEqual(response.status, 200, path)
            self.assertIn(kind, response.getheader("Content-Type"))
            self.assertIn("default-src 'none'", response.getheader("Content-Security-Policy"))
            self.assertIn("script-src 'self'", response.getheader("Content-Security-Policy"))
            self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            self.assertGreater(len(data), 100)

    def test_the_api_needs_a_token_and_says_what_to_do(self):
        response, body = self.json("GET", "/api/todo")
        self.assertEqual(response.status, 401)
        self.assertEqual(body["code"], "unauthenticated")
        self.assertTrue(body["message"] and body["next"])
        response, body = self.json("GET", "/api/todo", token="wrong")
        self.assertEqual(response.status, 401)
        response, body = self.json("GET", "/api/todo", token="good")
        self.assertEqual((response.status, body["items"][0]["case_id"]), (200, "abc"))

    def test_a_request_for_another_host_or_origin_is_refused(self):
        response, _ = self.request("GET", "/api/me", token="good", host="evil.example:80")
        self.assertEqual(response.status, 403)
        response, _ = self.request("GET", "/api/me", token="good", headers={"Origin": "http://evil.example"})
        self.assertEqual(response.status, 403)
        response, _ = self.request("GET", "/api/me", token="good", headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(response.status, 200)

    def test_a_missing_case_is_a_404_and_an_internal_error_leaks_nothing(self):
        response, body = self.json("GET", "/api/cases/missing", token="good")
        self.assertEqual((response.status, body["code"]), (404, "case_not_accessible"))
        response, body = self.json("GET", "/api/cases/boom", token="good")
        self.assertEqual(response.status, 500)
        self.assertNotIn("secret internal detail", json.dumps(body))
        self.assertEqual(body["code"], "internal")

    def test_a_decision_is_posted_as_json_and_reaches_the_service_unchanged(self):
        self.stub.calls.clear()
        response, _ = self.json("POST", "/api/cases/abc/review", token="good", body={"verdict": "approved", "digest": "d" * 64})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.stub.calls, [("review", "abc", "approved", "d" * 64)])
        self.json("POST", "/api/cases/abc/resolve", token="good", body={"kind": "close"})
        self.json("POST", "/api/authority/g1/revoke", token="good", body={"reason": "because"})
        self.assertEqual(self.stub.calls[1:], [("resolve", "abc", "close"), ("revoke", "g1", "because")])

    def test_a_post_must_be_small_valid_json(self):
        response, body = self.json("POST", "/api/cases/abc/review", token="good", raw=b"{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(response.status, 415)
        response, _ = self.json("POST", "/api/cases/abc/review", token="good", raw=b"not json")
        self.assertEqual(response.status, 400)
        response, _ = self.json("POST", "/api/cases/abc/review", token="good", raw=b"[1]")
        self.assertEqual(response.status, 400)
        response, _ = self.json("POST", "/api/cases/abc/review", token="good", raw=b"{" + b" " * 5000 + b"}")
        self.assertEqual(response.status, 400)

    def test_an_id_with_odd_characters_never_reaches_the_service(self):
        response, body = self.json("GET", "/api/cases/..%2F..%2Fetc", token="good")
        self.assertEqual(response.status, 404)
        response, body = self.json("GET", "/api/cases/a%20b", token="good")
        self.assertEqual(response.status, 404)

    def test_the_new_screens_have_their_own_reads(self):
        for path in ("/api/conversations", "/api/rules", "/api/staff", "/api/quality/run-1"):
            response, body = self.json("GET", path, token="good")
            self.assertEqual(response.status, 200, path)
            self.assertIsInstance(body, dict, path)
        response, _ = self.json("GET", "/api/quality/run%201", token="good")
        self.assertEqual(response.status, 404)

    def test_a_replay_and_a_receipt_name_the_case_and_the_decision(self):
        self.stub.calls.clear()
        response, body = self.json("POST", "/api/cases/abc/replay", token="good", body={"decision": "d1"})
        self.assertEqual((response.status, body["reproduced"]), (200, True))
        response, data = self.request("POST", "/api/cases/abc/receipt", token="good", body={"decision": "d1", "format": "svg"})
        self.assertEqual(response.status, 200)
        self.assertEqual(data, b"<svg></svg>")
        self.assertEqual(response.getheader("Content-Type"), "image/svg+xml")
        self.assertEqual(response.getheader("Content-Disposition"), 'attachment; filename="decision-abc.svg"')
        self.assertIn("sandbox", response.getheader("Content-Security-Policy"))
        self.assertEqual(self.stub.calls, [("replay", "abc", "d1"), ("receipt", "abc", "d1", "svg")])
        response, body = self.json("POST", "/api/cases/abc/replay", token="good", body={"decision": "../x"})
        self.assertEqual((response.status, body["code"]), (400, "decision_not_in_case"))
        self.assertEqual(len(self.stub.calls), 2)

    def test_a_grant_is_approved_and_activated_with_typed_values(self):
        self.stub.calls.clear()
        self.json("POST", "/api/authority", token="good", body={"intent": "citywheels.cancellation", "tier": "assist", "max_amount_minor": 50000, "days": 30, "acknowledged": False})
        self.json("POST", "/api/authority/g1/activate", token="good", body={})
        self.assertEqual(self.stub.calls, [("grant", "citywheels.cancellation", "assist", 50000, 30, False), ("activate", "g1")])
        response, _ = self.json("POST", "/api/authority", body={"tier": "assist"})
        self.assertEqual(response.status, 401)

    def test_the_session_cookie_is_only_for_evaluation_pages(self):
        response, _ = self.json("POST", "/api/session", body={"token": "good"})
        cookie = response.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/evals/", cookie)
        response, _ = self.json("POST", "/api/session", body={"token": "bad"})
        self.assertEqual(response.status, 401)
        self.assertIsNone(response.getheader("Set-Cookie"))

    def test_signing_out_clears_the_evaluation_cookie_without_needing_a_token(self):
        response, body = self.json("POST", "/api/signout", body={})
        self.assertEqual((response.status, body["ok"]), (200, True))
        cookie = response.getheader("Set-Cookie")
        self.assertIn("Max-Age=0", cookie)
        self.assertIn("Path=/evals/", cookie)

    def test_an_evaluation_page_needs_the_cookie_and_gets_its_own_policy(self):
        response, _ = self.request("GET", "/evals/run-1/dashboard.html")
        self.assertEqual(response.status, 401)
        response, data = self.request("GET", "/evals/run-1/dashboard.html", headers={"Cookie": "cockpit=good"})
        self.assertEqual(response.status, 200)
        self.assertIn(b"<script>1</script>", data)
        policy = response.getheader("Content-Security-Policy")
        self.assertIn("script-src 'unsafe-inline'", policy)
        self.assertNotIn("connect-src", policy)
        self.assertIn("default-src 'none'", policy)

    def test_an_evaluation_path_cannot_leave_the_runs_folder_or_read_other_files(self):
        cookie = {"Cookie": "cockpit=good"}
        for path in ("/evals/run-1/report.json", "/evals/../outside.html", "/evals/run-1/../../outside.html", "/evals/%2e%2e/dashboard.html", "/evals/run-1", "/evals/run-1/dashboard.html/x"):
            response, _ = self.request("GET", path, headers=cookie)
            self.assertEqual(response.status, 404, path)


if __name__ == "__main__":
    unittest.main()

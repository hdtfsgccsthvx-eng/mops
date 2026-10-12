"""Integration tests for protected web panel; require aiosqlite and httpx."""
import importlib
import importlib.util
import os
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient


@unittest.skipUnless(importlib.util.find_spec("aiosqlite"),
                     "Install application requirements to run web tests")
class DashboardIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "DATABASE_URL": "sqlite+aiosqlite:///" + self.tmp.name + "/test.sqlite",
            "SESSION_SECRET": "test-secret-for-tests-only",
            "WEB_ADMIN_USER": "admin",
            "WEB_ADMIN_PASSWORD": "test-password",
            "BOT_TOKEN": "", "ADMIN_IDS": ""
        })
        self.env.start()
        from fa_base import server
        self.server = importlib.reload(server)
        self.client = TestClient(self.server.app, follow_redirects=False, base_url="https://testserver")
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.tmp.cleanup()

    def login(self):
        r = self.client.post("/admin/login", data={"username": "admin", "password": "test-password"})
        self.assertEqual(r.status_code, 303)

    def test_denied_by_default(self):
        self.assertEqual(self.client.get("/admin").status_code, 303)
        self.assertEqual(self.client.get("/health").json()["status"], "degraded")
        self.assertEqual(self.client.post("/admin/garants", data={}).status_code, 422)

    def test_login_and_guarant_workflow(self):
        self.login()
        r = self.client.get("/admin/garants")
        self.assertEqual(r.status_code, 200)
        from re import search
        csrf = search(r'name="csrf" value="([^"]+)"', r.text).group(1)
        r = self.client.post("/admin/garants", data={
            "csrf": csrf, "handle": "test_garant", "country": "Сербия",
            "channel": "https://t.me/test_garant", "fee": "100 ₽", "description": "Тест"
        })
        self.assertEqual(r.status_code, 303)
        self.assertIn("@test_garant", self.client.get("/admin/garants").text)
        self.assertEqual(self.client.post("/admin/garants", data={
            "csrf": "INVALID", "handle": "foo_test", "country": "Сербия", "fee": "50 ₽"
        }).status_code, 403)


if __name__ == "__main__":
    unittest.main()

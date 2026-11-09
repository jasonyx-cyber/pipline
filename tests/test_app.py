import json
import os
import unittest
from unittest.mock import patch

from app import app


USER = {"id": "owner-1", "email": "person@example.com", "email_confirmed_at": "2026-01-01T00:00:00Z"}
SESSION = {"access_token": "private-access-token", "refresh_token": "private-refresh-token", "user": USER}


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.payload = payload
        self.content = json.dumps(payload).encode() if payload is not None else b""

    def json(self):
        return self.payload


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"SUPABASE_URL": "http://localhost:54321", "SUPABASE_ANON_KEY": "test-publishable-key"},
        )
        self.environment.start()
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def tearDown(self):
        self.environment.stop()

    def test_home_and_assets_are_served_with_security_headers(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Pipeline", page.data)
        self.assertIn(b"/static/app.js?v=3", page.data)
        self.assertIn(b'id="recoveryForm" method="post" action="/" hidden', page.data)
        self.assertIn(b'id="authForm" method="post" action="/"', page.data)
        self.assertIn("frame-ancestors 'none'", page.headers["Content-Security-Policy"])
        page.close()
        asset = self.client.get("/static/app.js")
        self.assertEqual(asset.status_code, 200)
        self.assertIn(b"Account access isn't connected yet", asset.data)
        self.assertNotIn(b'$("#authSubmit").disabled = true', asset.data)
        self.assertIn("no-cache", asset.headers["Cache-Control"])
        asset.close()

    def test_health_does_not_expose_configuration_secrets(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"SECRET_KEY", response.data)
        self.assertNotIn(b"SUPABASE_ANON_KEY", response.data)

    def test_mutations_require_a_same_origin(self):
        response = self.client.post("/api/auth/login", json={"email": "person@example.com", "password": "irrelevant"})
        self.assertEqual(response.status_code, 403)

    @patch("app.requests.request")
    def test_login_stores_tokens_only_in_an_encrypted_http_only_cookie(self, upstream):
        def respond(method, url, **_kwargs):
            if url.endswith("/auth/v1/token"):
                return FakeResponse(200, SESSION)
            if url.endswith("/auth/v1/user"):
                return FakeResponse(200, USER)
            if url.endswith("/rest/v1/applications"):
                return FakeResponse(200, [])
            raise AssertionError(f"Unexpected upstream request: {method} {url}")

        upstream.side_effect = respond
        response = self.client.post(
            "/api/auth/login",
            headers={"Origin": "http://localhost"},
            json={"email": "person@example.com", "password": "a-correct-password"},
        )
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("private-access-token", cookie)
        self.assertNotIn("private-refresh-token", cookie)

        apps = self.client.get("/api/applications")
        self.assertEqual(apps.status_code, 200)
        query = next(call.kwargs["params"] for call in upstream.call_args_list if call.args[1].endswith("/rest/v1/applications"))
        self.assertEqual(query["user_id"], "eq.owner-1")

    @patch("app.requests.request")
    def test_production_session_cookie_is_secure(self, upstream):
        upstream.return_value = FakeResponse(200, SESSION)
        with patch.dict(os.environ, {"VERCEL": "1", "APP_ORIGIN": "https://app.example"}):
            response = self.client.post(
                "/api/auth/login",
                headers={"Origin": "https://app.example"},
                json={"email": "person@example.com", "password": "a-correct-password"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Secure", response.headers["Set-Cookie"])

    @patch("app.requests.request")
    def test_unconfirmed_accounts_cannot_create_a_session(self, upstream):
        unconfirmed = {**SESSION, "user": {"id": "owner-1", "email": "person@example.com"}}
        upstream.return_value = FakeResponse(200, unconfirmed)
        response = self.client.post(
            "/api/auth/login",
            headers={"Origin": "http://localhost"},
            json={"email": "person@example.com", "password": "a-correct-password"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("Set-Cookie", response.headers)

    @patch("app.requests.request")
    def test_signup_requires_email_verification_before_setting_a_cookie(self, upstream):
        upstream.return_value = FakeResponse(
            200,
            {
                **SESSION,
                "user": {"id": "owner-1", "email": "person@example.com"},
            },
        )
        response = self.client.post(
            "/api/auth/signup",
            headers={"Origin": "http://localhost"},
            json={"email": "person@example.com", "password": "a-correct-password"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.json["verified"])
        self.assertNotIn("Set-Cookie", response.headers)

    @patch("app.requests.request")
    def test_application_writes_are_scoped_to_the_verified_owner(self, upstream):
        created = {
            "id": "application-1",
            "company": "Example Co",
            "role": "Engineer",
            "link": "",
            "notes": "",
            "status": "Applied",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }

        def respond(method, url, **_kwargs):
            if url.endswith("/auth/v1/token"):
                return FakeResponse(200, SESSION)
            if url.endswith("/auth/v1/user"):
                return FakeResponse(200, USER)
            if url.endswith("/rest/v1/applications"):
                return FakeResponse(201, [created])
            raise AssertionError(f"Unexpected upstream request: {method} {url}")

        upstream.side_effect = respond
        self.client.post(
            "/api/auth/login",
            headers={"Origin": "http://localhost"},
            json={"email": "person@example.com", "password": "a-correct-password"},
        )
        response = self.client.post(
            "/api/applications",
            headers={"Origin": "http://localhost"},
            json={"company": "Example Co", "role": "Engineer"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("user_id", response.json["application"])
        write = next(call for call in upstream.call_args_list if call.args[1].endswith("/rest/v1/applications"))
        self.assertEqual(write.kwargs["json"]["user_id"], USER["id"])
        self.assertEqual(write.kwargs["headers"]["Authorization"], "Bearer private-access-token")

    @patch("app.requests.request")
    def test_invalid_application_is_rejected_before_storage(self, upstream):
        def respond(method, url, **_kwargs):
            if url.endswith("/auth/v1/token"):
                return FakeResponse(200, SESSION)
            if url.endswith("/auth/v1/user"):
                return FakeResponse(200, USER)
            raise AssertionError(f"Unexpected upstream request: {method} {url}")

        upstream.side_effect = respond
        login = self.client.post(
            "/api/auth/login",
            headers={"Origin": "http://localhost"},
            json={"email": "person@example.com", "password": "a-correct-password"},
        )
        self.assertEqual(login.status_code, 200)
        response = self.client.post(
            "/api/applications",
            headers={"Origin": "http://localhost"},
            json={"company": "x" * 161, "role": "Engineer"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(any(call.args[1].endswith("/rest/v1/applications") for call in upstream.call_args_list))

    def test_storage_is_unavailable_without_supabase_configuration(self):
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_ANON_KEY": ""}):
            response = self.client.get("/api/config")
        self.assertFalse(response.json["configured"])


if __name__ == "__main__":
    unittest.main()

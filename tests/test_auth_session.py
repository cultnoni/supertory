"""Local auth session file + /api/auth routes (no live Supabase)."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sync.auth_session as auth_session
import sync.supabase_client as supabase_client


class AuthSessionFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        self.resolve_patch = patch.object(
            auth_session, "auth_session_path", return_value=self.data_dir / "auth_session.json"
        )
        self.resolve_patch.start()

    def tearDown(self) -> None:
        self.resolve_patch.stop()
        self.tmp.cleanup()

    def test_save_load_roundtrip_without_password(self) -> None:
        path = auth_session.save_session("acc-1", "ref-1", "user-1", "a@example.com")
        self.assertTrue(path.is_file())
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("password", saved)
        self.assertEqual(saved["access_token"], "acc-1")
        self.assertEqual(saved["refresh_token"], "ref-1")
        loaded = auth_session.load_session()
        self.assertEqual(loaded["email"], "a@example.com")
        self.assertEqual(loaded["user_id"], "user-1")

    def test_load_missing_or_corrupt_returns_none(self) -> None:
        self.assertIsNone(auth_session.load_session())
        path = self.data_dir / "auth_session.json"
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(auth_session.load_session())
        path.write_text(json.dumps({"access_token": "", "refresh_token": "x"}), encoding="utf-8")
        self.assertIsNone(auth_session.load_session())

    def test_clear_session_deletes_file(self) -> None:
        auth_session.save_session("a", "b", "c", "d@e.com")
        auth_session.clear_session()
        self.assertFalse((self.data_dir / "auth_session.json").exists())
        auth_session.clear_session()  # missing file is fine


class _FakeAuth:
    def __init__(self, session=None, user=None, fail_set=False, fail_sign_in=False):
        self.session = session
        self.user = user
        self.fail_set = fail_set
        self.fail_sign_in = fail_sign_in
        self.signed_out = False
        self.set_calls = []

    def set_session(self, access_token, refresh_token):
        self.set_calls.append((access_token, refresh_token))
        if self.fail_set:
            raise RuntimeError("expired")
        return SimpleNamespace(session=self.session, user=self.user)

    def sign_in_with_password(self, credentials):
        if self.fail_sign_in:
            raise RuntimeError("Invalid login credentials")
        return SimpleNamespace(session=self.session, user=self.user)

    def sign_up(self, credentials):
        return SimpleNamespace(session=self.session, user=self.user)

    def sign_out(self):
        self.signed_out = True

    def get_user(self):
        return SimpleNamespace(user=self.user)

    def on_auth_state_change(self, _callback):
        return None


class _FakeClient:
    def __init__(self, auth):
        self.auth = auth


class RestoreAndAuthFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        self.path = self.data_dir / "auth_session.json"
        self.path_patch = patch.object(auth_session, "auth_session_path", return_value=self.path)
        self.path_patch.start()
        supabase_client.reset_supabase_client_cache()

    def tearDown(self) -> None:
        supabase_client.reset_supabase_client_cache()
        self.path_patch.stop()
        self.tmp.cleanup()

    def _session(self, access="new-acc", refresh="new-ref", email="a@example.com"):
        user = SimpleNamespace(id="user-1", email=email)
        return SimpleNamespace(
            access_token=access,
            refresh_token=refresh,
            user=user,
        )

    def test_restore_session_rotates_and_saves_tokens(self) -> None:
        auth_session.save_session("old-acc", "old-ref", "user-1", "a@example.com")
        session = self._session()
        client = _FakeClient(_FakeAuth(session=session, user=session.user))
        with patch.object(supabase_client, "_create_anon_client", return_value=client):
            restored = supabase_client.restore_session()
        self.assertIs(restored, client)
        saved = auth_session.load_session()
        self.assertEqual(saved["access_token"], "new-acc")
        self.assertEqual(saved["refresh_token"], "new-ref")

    def test_restore_session_clears_file_on_failure(self) -> None:
        auth_session.save_session("old-acc", "old-ref", "user-1", "a@example.com")
        client = _FakeClient(_FakeAuth(fail_set=True))
        with patch.object(supabase_client, "_create_anon_client", return_value=client):
            self.assertIsNone(supabase_client.restore_session())
        self.assertIsNone(auth_session.load_session())

    def test_sign_up_needs_email_confirmation_when_session_missing(self) -> None:
        user = SimpleNamespace(id="user-1", email="a@example.com")
        client = _FakeClient(_FakeAuth(session=None, user=user))
        with patch.object(supabase_client, "_create_anon_client", return_value=client):
            result = supabase_client.sign_up("a@example.com", "secret1")
        self.assertTrue(result["ok"])
        self.assertTrue(result["needs_email_confirmation"])
        self.assertIn("확인 이메일", result["message"])
        self.assertIsNone(auth_session.load_session())

    def test_sign_in_saves_session(self) -> None:
        session = self._session()
        client = _FakeClient(_FakeAuth(session=session, user=session.user))
        with patch.object(supabase_client, "_create_anon_client", return_value=client):
            result = supabase_client.sign_in("a@example.com", "secret1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["user"]["email"], "a@example.com")
        saved = auth_session.load_session()
        self.assertEqual(saved["access_token"], "new-acc")

    def test_get_current_user_requires_session_file(self) -> None:
        session = self._session()
        client = _FakeClient(_FakeAuth(session=session, user=session.user))
        supabase_client._set_client(client)
        self.assertIsNone(supabase_client.get_current_user())

    def test_get_supabase_client_prefers_restored_session(self) -> None:
        auth_session.save_session("old-acc", "old-ref", "user-1", "a@example.com")
        session = self._session()
        user_client = _FakeClient(_FakeAuth(session=session, user=session.user))
        with patch.object(supabase_client, "_create_anon_client", return_value=user_client):
            client = supabase_client.get_supabase_client()
        self.assertIs(client, user_client)

    def test_get_supabase_client_returns_none_when_logged_out(self) -> None:
        self.assertIsNone(supabase_client.get_supabase_client())

    def test_sign_out_leaves_logged_out_state(self) -> None:
        auth_session.save_session("acc", "ref", "user-1", "a@example.com")
        session = self._session()
        client = _FakeClient(_FakeAuth(session=session, user=session.user))
        supabase_client._set_client(client)
        result = supabase_client.sign_out()
        self.assertTrue(result["ok"])
        self.assertTrue(client.auth.signed_out)
        self.assertIsNone(auth_session.load_session())
        self.assertIsNone(supabase_client.get_supabase_client())


class AuthApiTests(unittest.TestCase):
    def setUp(self) -> None:
        import app

        self.app = app
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        supabase_client.reset_supabase_client_cache()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.app.DATA_DIR = self.original_data_dir
        self.app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()
        supabase_client.reset_supabase_client_cache()

    def _request(self, method, path, body=None):
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        connection.request(method, path, payload, headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        data = json.loads(raw) if raw else {}
        return response.status, data

    def test_me_returns_null_when_logged_out(self) -> None:
        with patch.object(self.app, "get_current_user", return_value=None):
            status, body = self._request("GET", "/api/auth/me")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body, {"user": None})

    def test_login_and_logout_routes(self) -> None:
        with patch.object(
            self.app,
            "sign_in",
            return_value={"ok": True, "user": {"id": "u1", "email": "a@example.com"}},
        ):
            status, body = self._request(
                "POST",
                "/api/auth/login",
                {"email": "a@example.com", "password": "secret1"},
            )
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body["user"]["email"], "a@example.com")

        with patch.object(self.app, "sign_out", return_value={"ok": True}):
            status, body = self._request("POST", "/api/auth/logout", {})
        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(body["ok"])

    def test_signup_confirmation_status(self) -> None:
        with patch.object(
            self.app,
            "sign_up",
            return_value={
                "ok": True,
                "needs_email_confirmation": True,
                "message": "확인 이메일을 확인해 주세요.",
                "user": {"id": "u1", "email": "a@example.com"},
            },
        ):
            status, body = self._request(
                "POST",
                "/api/auth/signup",
                {"email": "a@example.com", "password": "secret1"},
            )
        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(body["needs_email_confirmation"])

    def test_login_validation_error(self) -> None:
        with patch.object(
            self.app,
            "sign_in",
            return_value={"ok": False, "error": "이메일 또는 비밀번호가 올바르지 않습니다."},
        ):
            status, body = self._request(
                "POST",
                "/api/auth/login",
                {"email": "a@example.com", "password": "nope"},
            )
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()

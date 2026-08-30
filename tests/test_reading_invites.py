"""Reading-invite snapshots: selected scenes, author-note strip, revoke."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

import app
from sync import reading_invites

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "supabase" / "migrations" / "20260831090000_reading_invites.sql"

AUTHOR_NOTE_HTML = (
    "<p>공개 본문입니다.</p>"
    '<p class="st-author-note" data-author-note="1">비밀 스포일러 주석</p>'
    "<p>다음 문단.</p>"
)


class FakeResult:
    def __init__(self, data=None):
        self.data = data or []


class FakeQuery:
    def __init__(self, store):
        self.store = store
        self._eq = {}
        self._in = None
        self._op = "select"
        self._payload = None

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def eq(self, key, value):
        self._eq[key] = value
        return self

    def in_(self, key, values):
        self._in = (key, list(values))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = dict(payload)
        return self

    def execute(self):
        if self._op == "select":
            matches = list(self.store.rows)
            for key, value in self._eq.items():
                matches = [row for row in matches if row.get(key) == value]
            if self._in:
                key, values = self._in
                matches = [row for row in matches if row.get(key) in values]
            return FakeResult(matches)
        if self._op == "insert":
            payloads = self._payload if isinstance(self._payload, list) else [self._payload]
            inserted = []
            for payload in payloads:
                row = dict(payload)
                self.store.rows.append(row)
                self.store.inserts.append(row)
                inserted.append(row)
            return FakeResult(inserted)
        if self._op == "update":
            updated = []
            for index, existing in enumerate(self.store.rows):
                if all(existing.get(key) == value for key, value in self._eq.items()):
                    merged = dict(existing)
                    merged.update(self._payload)
                    self.store.rows[index] = merged
                    updated.append(merged)
            self.store.updates.extend(updated)
            return FakeResult(updated)
        return FakeResult([])


class FakeStore:
    def __init__(self):
        self.rows = []
        self.inserts = []
        self.updates = []


class FakeClient:
    def __init__(self):
        self.tables = {
            "reading_invites": FakeStore(),
            "reading_invite_scenes": FakeStore(),
            "reading_invite_comments": FakeStore(),
        }

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = FakeStore()
        return FakeQuery(self.tables[name])


class ReadingInviteMigrationTests(unittest.TestCase):
    def test_schema_and_rls_lock_anon_table_select(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        lower = sql.lower()
        self.assertIn("create table if not exists public.reading_invites", lower)
        self.assertIn("create table if not exists public.reading_invite_scenes", lower)
        self.assertIn("create table if not exists public.reading_invite_comments", lower)
        self.assertIn("enable row level security", lower)
        self.assertIn("create unique index if not exists reading_invites_token_uidx", lower)
        self.assertIn("get_reading_invite(p_token text)", lower)
        self.assertIn("security definer", lower)
        self.assertIn("revoke all on public.reading_invites from anon", lower)
        self.assertNotRegex(
            sql,
            r"create policy \S+_select_anon",
        )
        self.assertIn("grant execute on function public.get_reading_invite(text) to anon", lower)
        self.assertIn("status = 'active'", lower)
        self.assertIn("expires_at > timezone('utc', now())", lower)


class ReadingInviteServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeClient()
        self.user = {"id": "11111111-1111-1111-1111-111111111111"}
        self.now = datetime(2026, 8, 31, 3, 0, tzinfo=timezone.utc)

    def test_create_strips_author_notes_and_only_selected_scenes(self) -> None:
        scenes = [
            {
                "order_index": 0,
                "scene_title": "1화",
                "content_snapshot": app.plain_text_from_content(AUTHOR_NOTE_HTML),
                "local_scene_id": 11,
            },
            {
                "order_index": 1,
                "scene_title": "3화",
                "content_snapshot": app.plain_text_from_content("<p>선택 본문</p>"),
                "local_scene_id": 13,
            },
        ]
        invite = reading_invites.create_invite(
            project_id=7,
            title="테스트 작품",
            scenes=scenes,
            user=self.user,
            client=self.client,
            now=self.now,
        )
        self.assertTrue(invite["token"])
        self.assertIn("/read/", invite["public_url"])
        self.assertEqual(invite["status"], "active")
        uploaded = self.client.tables["reading_invite_scenes"].inserts
        self.assertEqual(len(uploaded), 2)
        self.assertEqual([row["scene_title"] for row in uploaded], ["1화", "3화"])
        self.assertEqual([row["local_scene_id"] for row in uploaded], [11, 13])
        joined = "\n".join(row["content_snapshot"] for row in uploaded)
        self.assertIn("공개 본문입니다.", joined)
        self.assertIn("다음 문단.", joined)
        self.assertIn("선택 본문", joined)
        self.assertNotIn("비밀 스포일러 주석", joined)
        self.assertNotIn("2화", " ".join(row["scene_title"] for row in uploaded))

    def test_tokens_are_unique_across_creates(self) -> None:
        scene = {
            "order_index": 0,
            "scene_title": "1화",
            "content_snapshot": "본문",
            "local_scene_id": 1,
        }
        first = reading_invites.create_invite(
            project_id=1, title="A", scenes=[scene], user=self.user, client=self.client, now=self.now
        )
        second = reading_invites.create_invite(
            project_id=1, title="A", scenes=[scene], user=self.user, client=self.client, now=self.now
        )
        self.assertNotEqual(first["token"], second["token"])
        tokens = [row["token"] for row in self.client.tables["reading_invites"].rows]
        self.assertEqual(len(tokens), len(set(tokens)))

    def test_revoke_sets_status_revoked(self) -> None:
        scene = {
            "order_index": 0,
            "scene_title": "1화",
            "content_snapshot": "본문",
            "local_scene_id": 1,
        }
        invite = reading_invites.create_invite(
            project_id=3, title="작품", scenes=[scene], user=self.user, client=self.client, now=self.now
        )
        revoked = reading_invites.revoke_invite(
            invite_id=invite["id"], user=self.user, client=self.client
        )
        self.assertEqual(revoked["status"], "revoked")
        self.assertEqual(revoked["display_status"], "revoked")
        stored = self.client.tables["reading_invites"].rows[0]
        self.assertEqual(stored["status"], "revoked")

    def test_other_owner_cannot_revoke(self) -> None:
        scene = {
            "order_index": 0,
            "scene_title": "1화",
            "content_snapshot": "본문",
            "local_scene_id": 1,
        }
        invite = reading_invites.create_invite(
            project_id=3, title="작품", scenes=[scene], user=self.user, client=self.client, now=self.now
        )
        with self.assertRaises(reading_invites.ReadingInviteError) as raised:
            reading_invites.revoke_invite(
                invite_id=invite["id"],
                user={"id": "22222222-2222-2222-2222-222222222222"},
                client=self.client,
            )
        self.assertEqual(raised.exception.status, "not_found")
        self.assertEqual(self.client.tables["reading_invites"].rows[0]["status"], "active")

    def test_expired_display_status_does_not_change_row(self) -> None:
        row = {
            "status": "active",
            "expires_at": (self.now - timedelta(days=1)).isoformat(),
        }
        self.assertEqual(reading_invites.display_status(row, now=self.now), "expired")

    def test_comments_group_by_scene(self) -> None:
        invite_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        scene_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        self.client.tables["reading_invites"].rows.append(
            {
                "id": invite_id,
                "user_id": self.user["id"],
                "token": "tok",
                "project_local_id": 9,
                "title": "작품",
                "status": "active",
                "created_at": self.now.isoformat(),
                "expires_at": (self.now + timedelta(days=30)).isoformat(),
            }
        )
        self.client.tables["reading_invite_scenes"].rows.append(
            {
                "id": scene_id,
                "invite_id": invite_id,
                "order_index": 0,
                "scene_title": "1화",
                "local_scene_id": 11,
            }
        )
        self.client.tables["reading_invite_comments"].rows.append(
            {
                "id": "c1",
                "invite_id": invite_id,
                "invite_scene_id": scene_id,
                "author_name": "",
                "content": "좋았어요",
                "created_at": self.now.isoformat(),
            }
        )
        payload = reading_invites.list_invite_comments(
            invite_id=invite_id, user=self.user, client=self.client
        )
        self.assertEqual(payload["scenes"][0]["scene_title"], "1화")
        self.assertEqual(payload["scenes"][0]["comments"][0]["content"], "좋았어요")
        self.assertEqual(payload["scenes"][0]["comments"][0]["author_name"], "")


class ReadingInviteApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = FakeClient()
        self.user = {"id": "11111111-1111-1111-1111-111111111111"}

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def _request(self, method, path, body=None):
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        connection.request(method, path, payload, headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        return response.status, json.loads(raw)

    def _seed_project(self):
        status, project = self._request("POST", "/api/projects", {"title": "초대 테스트", "main_genre": "판타지"})
        self.assertEqual(status, 201, project)
        project_id = project["id"]
        status, chapter = self._request("POST", f"/api/projects/{project_id}/chapters", {"title": "1장"})
        self.assertEqual(status, 201)
        status, scene_a = self._request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"}
        )
        self.assertEqual(status, 201)
        status, scene_b = self._request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "2화"}
        )
        self.assertEqual(status, 201)
        status, detail_a = self._request("GET", f"/api/scenes/{scene_a['id']}")
        self.assertEqual(status, 200)
        self._request("PUT", f"/api/scenes/{scene_a['id']}", {
            "title": "1화",
            "status": "complete",
            "synopsis_md": "",
            "notes_md": "",
            "content_md": AUTHOR_NOTE_HTML,
            "row_version": detail_a["row_version"],
        })
        status, detail_b = self._request("GET", f"/api/scenes/{scene_b['id']}")
        self.assertEqual(status, 200)
        self._request("PUT", f"/api/scenes/{scene_b['id']}", {
            "title": "2화",
            "status": "draft",
            "synopsis_md": "",
            "notes_md": "",
            "content_md": "<p>미완성 본문</p>",
            "row_version": detail_b["row_version"],
        })
        return project_id, scene_a["id"], scene_b["id"]

    def test_episodes_default_check_complete_only(self) -> None:
        project_id, scene_a, scene_b = self._seed_project()
        status, payload = self._request("GET", f"/api/projects/{project_id}/reading-invite-episodes")
        self.assertEqual(status, 200)
        by_id = {item["id"]: item for item in payload["episodes"]}
        self.assertTrue(by_id[scene_a]["checked"])
        self.assertFalse(by_id[scene_b]["checked"])

    def test_create_requires_login(self) -> None:
        project_id, scene_a, _scene_b = self._seed_project()
        with patch.object(app, "get_current_user", return_value=None):
            status, payload = self._request(
                "POST",
                f"/api/projects/{project_id}/reading-invites",
                {"scene_ids": [scene_a]},
            )
        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
        self.assertIn("로그인", payload["error"])

    def test_create_uploads_selected_snapshot_without_author_notes(self) -> None:
        project_id, scene_a, scene_b = self._seed_project()
        with (
            patch.object(app, "get_current_user", return_value=self.user),
            patch.object(app, "get_supabase_client", return_value=self.client),
            patch.object(reading_invites, "get_current_user", return_value=self.user),
            patch.object(reading_invites, "get_supabase_client", return_value=self.client),
        ):
            status, payload = self._request(
                "POST",
                f"/api/projects/{project_id}/reading-invites",
                {"scene_ids": [scene_a]},
            )
        self.assertEqual(status, 201, payload)
        uploaded = self.client.tables["reading_invite_scenes"].inserts
        self.assertEqual(len(uploaded), 1)
        self.assertEqual(uploaded[0]["local_scene_id"], scene_a)
        self.assertNotEqual(uploaded[0]["local_scene_id"], scene_b)
        self.assertIn("공개 본문입니다.", uploaded[0]["content_snapshot"])
        self.assertNotIn("비밀 스포일러 주석", uploaded[0]["content_snapshot"])
        self.assertTrue(payload["invite"]["token"])
        self.assertIn("https://supertory.com/read/", payload["invite"]["public_url"])

    def test_revoke_via_api(self) -> None:
        project_id, scene_a, _scene_b = self._seed_project()
        with (
            patch.object(app, "get_current_user", return_value=self.user),
            patch.object(app, "get_supabase_client", return_value=self.client),
            patch.object(reading_invites, "get_current_user", return_value=self.user),
            patch.object(reading_invites, "get_supabase_client", return_value=self.client),
        ):
            status, created = self._request(
                "POST",
                f"/api/projects/{project_id}/reading-invites",
                {"scene_ids": [scene_a]},
            )
            self.assertEqual(status, 201, created)
            invite_id = created["invite"]["id"]
            status, revoked = self._request("POST", f"/api/reading-invites/{invite_id}/revoke", {})
        self.assertEqual(status, 200, revoked)
        self.assertEqual(revoked["invite"]["status"], "revoked")
        self.assertEqual(self.client.tables["reading_invites"].rows[0]["status"], "revoked")

    def test_feedback_summary_counts_new_notes_and_latest_invite(self) -> None:
        invite_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        other_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        self.client.tables["reading_invites"].rows = [
            {
                "id": invite_id,
                "user_id": self.user["id"],
                "project_local_id": 1,
                "title": "달빛 정원",
            },
            {
                "id": other_id,
                "user_id": self.user["id"],
                "project_local_id": 1,
                "title": "다른 작품",
            },
        ]
        self.client.tables["reading_invite_comments"].rows = [
            {
                "id": "c1",
                "invite_id": invite_id,
                "created_at": "2026-08-31T02:00:00+00:00",
            },
            {
                "id": "c2",
                "invite_id": other_id,
                "created_at": "2026-08-31T03:00:00+00:00",
            },
            {
                "id": "c3",
                "invite_id": invite_id,
                "created_at": "2026-08-31T04:00:00+00:00",
            },
        ]
        summary = reading_invites.feedback_summary(
            project_id=1,
            since="2026-08-31T02:30:00+00:00",
            user=self.user,
            client=self.client,
        )
        self.assertEqual(summary["new_count"], 2)
        self.assertEqual(summary["invite_id"], invite_id)
        self.assertEqual(summary["title"], "달빛 정원")
        self.assertTrue(str(summary["latest_created_at"]).startswith("2026-08-31T04:00:00"))


if __name__ == "__main__":
    unittest.main()

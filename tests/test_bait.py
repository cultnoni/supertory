"""Bait (떡밥 던지기) DB CRUD, localStorage import, and snooze behavior."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import app


class BaitApiTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        return response.status, json.loads(raw)

    def _seed_project_with_scenes(self) -> dict:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "떡밥 테스트", "main_genre": "판타지"},
        )
        self.assertEqual(status, 201, project)
        pid = project["id"]
        status, chapter = self.request(
            "POST", f"/api/projects/{pid}/chapters", {"title": "1장"}
        )
        self.assertEqual(status, 201, chapter)
        scenes = []
        for title in ("1화 심기", "12화 회수"):
            status, scene = self.request(
                "POST",
                f"/api/chapters/{chapter['id']}/scenes",
                {"title": title},
            )
            self.assertEqual(status, 201, scene)
            scenes.append(scene)
        return {
            "project_id": pid,
            "chapter_id": chapter["id"],
            "plant_scene_id": scenes[0]["id"],
            "recover_scene_id": scenes[1]["id"],
        }

    def test_migration_023_creates_bait_table(self) -> None:
        with app.database() as connection:
            row = connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = 23"
            ).fetchone()
            self.assertIsNotNone(row)
            cols = {
                r[1]
                for r in connection.execute("PRAGMA table_info(bait)").fetchall()
            }
        self.assertIn("snooze_until", cols)
        self.assertIn("notify_on_recover", cols)
        self.assertIn("recover_scene_id", cols)

    def test_create_list_update_delete_bait(self) -> None:
        """(a) 떡밥 생성 → DB 저장 → 조회/수정/삭제."""
        seed = self._seed_project_with_scenes()
        pid = seed["project_id"]

        status, created = self.request(
            "POST",
            f"/api/projects/{pid}/baits",
            {
                "kind": "plant",
                "quote": "집사가 밤중에 외출했다.",
                "summary": "집사 외출",
                "plant_scene_id": seed["plant_scene_id"],
                "recover_scene_id": seed["recover_scene_id"],
                "notify_on_recover": True,
            },
        )
        self.assertEqual(status, 201, created)
        self.assertTrue(created["id"])
        self.assertEqual(created["quote"], "집사가 밤중에 외출했다.")
        self.assertEqual(created["recoverSceneId"], seed["recover_scene_id"])
        self.assertTrue(created["notifyOnRecover"])
        self.assertIsNone(created.get("snoozeUntil"))

        status, listed = self.request("GET", f"/api/projects/{pid}/baits")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], created["id"])

        # Direct SQLite check
        with app.database() as connection:
            row = connection.execute(
                "SELECT quote, recover_scene_id, notify_on_recover FROM bait WHERE id = ?",
                (created["id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["quote"], "집사가 밤중에 외출했다.")
        self.assertEqual(int(row["recover_scene_id"]), seed["recover_scene_id"])
        self.assertEqual(int(row["notify_on_recover"]), 1)

        status, updated = self.request(
            "PUT",
            f"/api/baits/{created['id']}",
            {"summary": "집사 밤 외출 단서", "notify_on_recover": False},
        )
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["summary"], "집사 밤 외출 단서")
        self.assertFalse(updated["notifyOnRecover"])
        # quote preserved
        self.assertEqual(updated["quote"], "집사가 밤중에 외출했다.")

        status, result = self.request("DELETE", f"/api/baits/{created['id']}")
        self.assertEqual(status, 200, result)
        self.assertTrue(result.get("ok"))
        status, empty = self.request("GET", f"/api/projects/{pid}/baits")
        self.assertEqual(status, 200)
        self.assertEqual(empty, [])

    def test_import_localstorage_shaped_items(self) -> None:
        """(b) localStorage 형태 items → /baits/import 로 DB 적재."""
        seed = self._seed_project_with_scenes()
        pid = seed["project_id"]
        local_items = [
            {
                "id": "bait-legacy-001",
                "kind": "plant",
                "quote": "레거시 로컬 떡밥 문장",
                "summary": "레거시",
                "recoverSceneId": seed["recover_scene_id"],
                "plantSceneId": seed["plant_scene_id"],
                "sourceSceneId": seed["plant_scene_id"],
                "notifyOnRecover": True,
                "createdAt": "2026-01-01T00:00:00.000Z",
            },
            {
                "id": "bait-legacy-002",
                "kind": "idea",
                "quote": "아직 안 쓴 아이디어",
                "recoverAt": "후반부",
                "notifyOnRecover": False,
            },
        ]
        status, result = self.request(
            "POST",
            f"/api/projects/{pid}/baits/import",
            {"items": local_items},
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(result.get("total"), 2)

        status, listed = self.request("GET", f"/api/projects/{pid}/baits")
        self.assertEqual(status, 200)
        ids = {item["id"] for item in listed}
        self.assertIn("bait-legacy-001", ids)
        self.assertIn("bait-legacy-002", ids)
        plant = next(i for i in listed if i["id"] == "bait-legacy-001")
        self.assertEqual(plant["quote"], "레거시 로컬 떡밥 문장")
        self.assertEqual(plant["recoverSceneId"], seed["recover_scene_id"])

        # Re-import updates rather than duplicating
        status, result2 = self.request(
            "POST",
            f"/api/projects/{pid}/baits/import",
            {
                "items": [
                    {
                        **local_items[0],
                        "summary": "레거시 수정",
                    }
                ]
            },
        )
        self.assertEqual(status, 200, result2)
        self.assertEqual(result2.get("updated"), 1)
        status, listed2 = self.request("GET", f"/api/projects/{pid}/baits")
        plant2 = next(i for i in listed2 if i["id"] == "bait-legacy-001")
        self.assertEqual(plant2["summary"], "레거시 수정")
        self.assertEqual(len([i for i in listed2 if i["id"] == "bait-legacy-001"]), 1)

    def test_recover_scene_match_and_snooze(self) -> None:
        """(c)(d)(e) 회수 회차 매칭, 1일 미루기, 알림 끄기."""
        seed = self._seed_project_with_scenes()
        pid = seed["project_id"]
        recover_id = seed["recover_scene_id"]
        other_id = seed["plant_scene_id"]

        status, bait = self.request(
            "POST",
            f"/api/projects/{pid}/baits",
            {
                "quote": "복선 문장",
                "summary": "복선",
                "recover_scene_id": recover_id,
                "plant_scene_id": other_id,
                "notify_on_recover": True,
            },
        )
        self.assertEqual(status, 201, bait)
        bait_id = bait["id"]

        # (c) Due baits for recover scene
        status, listed = self.request("GET", f"/api/projects/{pid}/baits")
        due = [
            b
            for b in listed
            if b.get("recoverSceneId") == recover_id and b.get("notifyOnRecover")
        ]
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["id"], bait_id)

        # Not due on plant scene
        not_due = [
            b
            for b in listed
            if b.get("recoverSceneId") == other_id and b.get("notifyOnRecover")
        ]
        self.assertEqual(not_due, [])

        # (d) 1-day snooze
        until = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace(
            "+00:00", "Z"
        )
        status, snoozed = self.request(
            "PUT",
            f"/api/baits/{bait_id}",
            {"snooze_until": until},
        )
        self.assertEqual(status, 200, snoozed)
        self.assertTrue(snoozed.get("snoozeUntil"))

        # Simulate client isBaitSnoozedNow: parse ISO and compare
        snooze_ts = datetime.fromisoformat(
            str(snoozed["snoozeUntil"]).replace("Z", "+00:00")
        )
        self.assertGreater(snooze_ts, datetime.now(timezone.utc))

        # Still notify_on, but snoozed — client would suppress popup
        self.assertTrue(snoozed.get("notifyOnRecover"))

        # next_open mode
        status, next_open = self.request(
            "PUT",
            f"/api/baits/{bait_id}",
            {"snooze_until": "next_open"},
        )
        self.assertEqual(status, 200, next_open)
        self.assertEqual(next_open.get("snoozeUntil"), "next_open")

        # Clear next_open when leaving scene (server-side field clear)
        status, cleared = self.request(
            "PUT",
            f"/api/baits/{bait_id}",
            {"snooze_until": None},
        )
        self.assertEqual(status, 200, cleared)
        self.assertIsNone(cleared.get("snoozeUntil"))

        # (e) Turn notify off
        status, off = self.request(
            "PUT",
            f"/api/baits/{bait_id}",
            {"notify_on_recover": False},
        )
        self.assertEqual(status, 200, off)
        self.assertFalse(off.get("notifyOnRecover"))

        status, listed2 = self.request("GET", f"/api/projects/{pid}/baits")
        due_after = [
            b
            for b in listed2
            if b.get("recoverSceneId") == recover_id and b.get("notifyOnRecover")
        ]
        self.assertEqual(due_after, [])

    def test_invalid_recover_scene_id_rejected_on_create(self) -> None:
        """Create/update reject unknown scene ids; import soft-drops them."""
        seed = self._seed_project_with_scenes()
        pid = seed["project_id"]

        status, err = self.request(
            "POST",
            f"/api/projects/{pid}/baits",
            {
                "quote": "없는 회차로 회수",
                "recover_scene_id": 999999,
                "notify_on_recover": True,
            },
        )
        self.assertEqual(status, 400, err)
        self.assertIn("회차", str(err.get("error") or err))

        status, created = self.request(
            "POST",
            f"/api/projects/{pid}/baits",
            {
                "quote": "유효 회차 떡밥",
                "recover_scene_id": seed["recover_scene_id"],
            },
        )
        self.assertEqual(status, 201, created)
        status, bad_put = self.request(
            "PUT",
            f"/api/baits/{created['id']}",
            {"plant_scene_id": 888888},
        )
        self.assertEqual(status, 400, bad_put)

        # Import: unknown scene id is cleared, row still lands
        status, imp = self.request(
            "POST",
            f"/api/projects/{pid}/baits/import",
            {
                "items": [
                    {
                        "id": "bait-orphan-scene",
                        "quote": "마이그레이션 고아 회차",
                        "recoverSceneId": 777777,
                        "plantSceneId": seed["plant_scene_id"],
                    }
                ]
            },
        )
        self.assertEqual(status, 200, imp)
        status, listed = self.request("GET", f"/api/projects/{pid}/baits")
        orphan = next(i for i in listed if i["id"] == "bait-orphan-scene")
        self.assertIsNone(orphan.get("recoverSceneId"))
        self.assertEqual(orphan.get("plantSceneId"), seed["plant_scene_id"])


if __name__ == "__main__":
    unittest.main()

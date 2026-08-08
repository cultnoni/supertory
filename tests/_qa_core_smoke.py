"""Live API smoke for non-AI SuperTory features (manual QA substitute)."""

from __future__ import annotations

import base64
import http.client
import json
import sys
import tempfile
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app
import document_export
import document_import
from document_export import ManuscriptBlock

PASS = 0
FAIL = 0
NOTES: list[tuple[str, str, str]] = []  # severity, name, detail


def ok(name: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    print(f"  OK  {name}" + (f" — {detail}" if detail else ""))


def fail(severity: str, name: str, detail: str) -> None:
    global FAIL
    FAIL += 1
    NOTES.append((severity, name, detail))
    print(f"  FAIL[{severity}] {name} — {detail}")


def main() -> int:
    td = tempfile.TemporaryDirectory()
    app.DATA_DIR = Path(td.name) / "data"
    app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
    app.initialise_database()
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_port

    def req(method: str, path: str, body: dict | None = None, raw: bool = False):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        payload = None
        headers = {}
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, payload, headers)
        resp = conn.getresponse()
        data = resp.read()
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
        if raw:
            return resp.status, data, hdrs
        try:
            return resp.status, json.loads(data.decode("utf-8"))
        except Exception:
            return resp.status, data.decode("utf-8", errors="replace")

    print("\n=== A. Project / package / empty project ===")
    st, empty_projects = req("GET", "/api/projects")
    if st == 200 and isinstance(empty_projects, list):
        ok("list projects (empty ok)", f"n={len(empty_projects)}")
    else:
        fail("crash", "list projects", f"{st} {empty_projects}")

    # Missing main_genre should fail clearly
    st, err = req("POST", "/api/projects", {"title": "장르없음"})
    if st == 400:
        ok("project create requires main_genre", str(err)[:80])
    else:
        fail("minor", "project create without genre", f"expected 400 got {st}")

    st, project = req(
        "POST",
        "/api/projects",
        {"title": "QA 스모크 <>&\"' 작품", "main_genre": "판타지", "purpose": "novel"},
    )
    if st != 201:
        fail("crash", "create project", f"{st} {project}")
        server.shutdown()
        td.cleanup()
        return 1
    pid = project["id"]
    ok("create project with special chars title", f"id={pid}")
    if project.get("package_name") or project.get("package_path"):
        ok(".stg package created on project create")
    else:
        fail("misbehave", ".stg package fields", str(project)[:120])

    st, touch = req("POST", f"/api/projects/{pid}/touch-open", {})
    ok("touch-open", f"{st}") if st == 200 else fail("misbehave", "touch-open", f"{st}")

    print("\n=== B. Outline empty / chapters / scenes / move ===")
    st, outline = req("GET", f"/api/projects/{pid}/outline")
    if st == 200:
        chs = outline.get("chapters") or []
        ok("outline on new project", f"chapters={len(chs)}")
    else:
        fail("crash", "outline empty project", f"{st}")

    st, ch = req("POST", f"/api/projects/{pid}/chapters", {"title": "1장 시작"})
    if st != 201:
        fail("crash", "create chapter", f"{st} {ch}")
        server.shutdown()
        td.cleanup()
        return 1
    ok("create chapter")
    st, sc1 = req("POST", f"/api/chapters/{ch['id']}/scenes", {"title": "1화"})
    st, sc2 = req("POST", f"/api/chapters/{ch['id']}/scenes", {"title": "2화"})
    if st != 201:
        fail("crash", "create scene", f"{st}")
    else:
        ok("create scenes")

    st, detail = req("GET", f"/api/scenes/{sc1['id']}")
    long_text = ("비가 내렸다. " * 500) + "특수문자 <>\"&'…—★※\n" + ("끝.\n" * 50)
    st, saved = req(
        "PUT",
        f"/api/scenes/{sc1['id']}",
        {
            "title": "1화",
            "status": "draft",
            "content_md": f"<p>{long_text}</p>",
            "row_version": detail["row_version"],
        },
    )
    if st == 200:
        ok("save long scene content", f"chars≈{len(long_text)}")
    else:
        fail("crash", "save long scene", f"{st} {saved}")

    st, moved = req(
        "POST",
        f"/api/scenes/{sc2['id']}/move",
        {"before_scene_id": sc1["id"]},
    )
    if st == 200 and moved.get("moved"):
        ok("scene reorder move")
    else:
        fail("misbehave", "scene move", f"{st} {moved}")

    st, nested = req(
        "POST",
        f"/api/scenes/{sc2['id']}/move",
        {"parent_scene_id": sc1["id"], "chapter_id": ch["id"]},
    )
    if st == 200 and nested.get("parent_scene_id") == sc1["id"]:
        ok("scene nest under parent")
    else:
        fail("misbehave", "scene nest", f"{st} {nested}")

    st, ch_under = req(
        "POST",
        f"/api/projects/{pid}/chapters",
        {"title": "하위폴더", "parent_scene_id": sc1["id"]},
    )
    if st == 201:
        ok("chapter under scene")
    else:
        fail("misbehave", "chapter under scene", f"{st} {ch_under}")

    st, part = req("POST", f"/api/projects/{pid}/parts", {"title": "1부"})
    if st in (200, 201):
        ok("create part")
        st, cm = req("POST", f"/api/chapters/{ch['id']}/move", {"part_id": part["id"]})
        ok("chapter to part", str(st)) if st == 200 else fail("misbehave", "chapter to part", f"{st}")
    else:
        fail("misbehave", "create part", f"{st}")

    st, dup = req("POST", f"/api/scenes/{sc1['id']}/duplicate", {})
    if st in (200, 201) and dup.get("id"):
        ok("duplicate scene", f"id={dup['id']}")
    else:
        fail("misbehave", "duplicate scene", f"{st} {dup}")

    print("\n=== C. Settings codex fields ===")
    st, settings = req(
        "PUT",
        f"/api/projects/{pid}/settings",
        {
            "synopsis_md": "시놉시스 본문",
            "logline_md": "로그라인 한 줄",
            "outline_summary": "줄거리 개요: 시작-중반-결말 방향",
            "worldbuilding_md": "세계관 메모",
            "intro_md": "작품 소개",
            "intent_md": "기획 의도",
            "keywords": ["판타지", "테스트", "특수<>"],
            "tory_priority_md": "토리 우선순위 메모",
        },
    )
    if st == 200 and settings.get("logline_md") == "로그라인 한 줄":
        ok("settings put multi fields")
    else:
        fail("misbehave", "settings put", f"{st} {settings}")

    if settings.get("outline_summary") == "줄거리 개요: 시작-중반-결말 방향":
        ok("outline_summary field persists")
    else:
        fail("misbehave", "outline_summary", f"{settings.get('outline_summary')!r}")

    st, outline2 = req("GET", f"/api/projects/{pid}/outline")
    proj = outline2.get("project") or {}
    if (proj.get("logline_md") or "") == "로그라인 한 줄":
        ok("settings visible on outline.project")
    else:
        fail("misbehave", "outline.project settings", str({k: proj.get(k) for k in ("logline_md", "outline_summary", "keywords")}))

    print("\n=== D. Ideas / characters / portrait / cast ===")
    st, idea = req(
        "POST",
        f"/api/projects/{pid}/ideas",
        {"title": "아이디어", "body_md": "메모 내용", "color": "yellow"},
    )
    if st == 201:
        ok("create idea")
        st, _ = req("PUT", f"/api/ideas/{idea['id']}", {"title": "수정", "body_md": "x", "color": "blue"})
        ok("update idea") if st == 200 else fail("misbehave", "update idea", str(st))
        st, _ = req("DELETE", f"/api/ideas/{idea['id']}")
        ok("delete idea") if st == 200 else fail("misbehave", "delete idea", str(st))
    else:
        fail("misbehave", "create idea", f"{st} {idea}")

    st, char = req("POST", f"/api/projects/{pid}/characters", {"name": "서연"})
    if st != 201:
        fail("misbehave", "create character", f"{st} {char}")
    else:
        ok("create character")
        cid = char["id"]
        st, cdetail = req("GET", f"/api/characters/{cid}")
        ok("character detail") if st == 200 else fail("misbehave", "character detail", str(st))
        st, _ = req(
            "PUT",
            f"/api/characters/{cid}",
            {
                "name": "서연",
                "role": "protagonist",
                "short_description": "주인공",
                "profile_md": "설정",
                "strengths_md": "강점",
                "weaknesses_md": "약점",
                "author_notes_md": "메모",
                "row_version": cdetail["character"]["row_version"],
            },
        )
        ok("update character") if st == 200 else fail("misbehave", "update character", str(st))
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        st, portrait_resp = req(
            "POST",
            f"/api/characters/{cid}/portrait",
            {
                "filename": "a.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(png).decode("ascii"),
            },
        )
        if st == 200 and portrait_resp.get("portrait_url"):
            ok("character portrait upload")
            st, img, _ = req("GET", f"/api/characters/{cid}/portrait", raw=True)
            ok("character portrait get", f"bytes={len(img)}") if st == 200 and img[:1] == b"\x89" else fail(
                "misbehave", "portrait get", f"{st}"
            )
        else:
            fail("misbehave", "portrait upload", f"{st} {portrait_resp}")

        st, cast = req(
            "PUT",
            f"/api/scenes/{sc1['id']}/characters",
            {"character_ids": [cid], "pov_id": cid},
        )
        if st == 200:
            ok("scene cast link + POV")
            st, members = req("GET", f"/api/scenes/{sc1['id']}/characters")
            if st == 200 and any(m.get("character_id") == cid for m in members):
                ok("scene cast list")
            else:
                fail("misbehave", "scene cast list", f"{st} {members}")
        else:
            fail("misbehave", "scene cast put", f"{st} {cast}")

    print("\n=== E. Bait DB CRUD + notify fields + snooze ===")
    st, bait = req(
        "POST",
        f"/api/projects/{pid}/baits",
        {
            "kind": "plant",
            "quote": "문장 드래그 떡밥",
            "summary": "단서",
            "plant_scene_id": sc1["id"],
            "recover_scene_id": sc1["id"],
            "notify_on_recover": True,
        },
    )
    if st == 201:
        ok("bait create DB")
        bid = bait["id"]
        st, blist = req("GET", f"/api/projects/{pid}/baits")
        ok("bait list", f"n={len(blist)}") if st == 200 and any(b["id"] == bid for b in blist) else fail(
            "misbehave", "bait list", f"{st}"
        )
        from datetime import datetime, timedelta, timezone

        until = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
        st, sn = req("PUT", f"/api/baits/{bid}", {"snooze_until": until, "notify_on_recover": True})
        if st == 200 and sn.get("snoozeUntil"):
            ok("bait snooze 1d")
        else:
            fail("misbehave", "bait snooze", f"{st} {sn}")
        st, sn2 = req("PUT", f"/api/baits/{bid}", {"snooze_until": "next_open"})
        ok("bait snooze next_open") if st == 200 and sn2.get("snoozeUntil") == "next_open" else fail(
            "misbehave", "bait next_open", f"{st}"
        )
        st, off = req("PUT", f"/api/baits/{bid}", {"notify_on_recover": False, "snooze_until": None})
        ok("bait notify off") if st == 200 and off.get("notifyOnRecover") is False else fail(
            "misbehave", "bait notify off", f"{st}"
        )
        # import migration shape
        st, imp = req(
            "POST",
            f"/api/projects/{pid}/baits/import",
            {
                "items": [
                    {
                        "id": "bait-local-mig",
                        "quote": "localStorage 형태",
                        "recoverSceneId": sc1["id"],
                        "plantSceneId": sc1["id"],
                        "notifyOnRecover": True,
                    }
                ]
            },
        )
        ok("bait import API", str(imp)) if st == 200 else fail("misbehave", "bait import", f"{st} {imp}")
    else:
        fail("crash", "bait create", f"{st} {bait}")

    print("\n=== F. Import / export ===")
    blocks = [
        ManuscriptBlock(kind="paragraph", text=line)
        for line in ("1화", "짧은 본문입니다.", "2화", "둘째 본문.")
    ]
    docx = document_export.build_docx(blocks)
    st, imported = req(
        "POST",
        "/api/import",
        {
            "filename": "qa.docx",
            "content_base64": base64.b64encode(docx).decode("ascii"),
            "title": "가져오기 QA",
            "purpose": "novel",
            "main_genre": "판타지",
            "split_mode": "headings",
        },
    )
    if st in (200, 201) and imported.get("project_id"):
        ok("document import new project", f"scenes={imported.get('scene_ids')}")
        ipid = imported["project_id"]
    else:
        fail("crash", "document import", f"{st} {imported}")
        ipid = pid

    for fmt in ("docx", "hwpx", "txt"):
        st, data, headers = req(
            "POST",
            f"/api/projects/{ipid}/export",
            {"format": fmt, "save_to_folder": False},
            raw=True,
        )
        if st != 200:
            fail("crash", f"export {fmt}", f"status={st}")
            continue
        if fmt in ("docx", "hwpx"):
            if data[:2] == b"PK":
                ok(f"export {fmt}", f"bytes={len(data)}")
                try:
                    text = document_import.extract_document(f"out.{fmt}", data).text
                    if "본문" in text or "짧은" in text or len(text) > 5:
                        ok(f"export {fmt} re-extract")
                    else:
                        fail("minor", f"export {fmt} content empty?", text[:80])
                except Exception as e:
                    fail("misbehave", f"export {fmt} extract", str(e))
            else:
                fail("crash", f"export {fmt} not zip", data[:40].decode("latin1", errors="replace"))
        else:
            ok(f"export {fmt}", f"bytes={len(data)}")

    print("\n=== G. Proof clean / match unit-level (no live HWP file) ===")
    st, cleaned = req("POST", "/api/proof-clean", {"text": "본문입니다.\n\n[메모] 무시\n다음 문단."})
    if st == 200 and (cleaned.get("text") or cleaned.get("cleaned") or cleaned.get("ok") is not None or "본문" in str(cleaned)):
        ok("proof-clean API responds", str(cleaned)[:100])
    else:
        # endpoint might require different keys
        fail("minor", "proof-clean shape", f"{st} {str(cleaned)[:120]}")

    print("\n=== H. Writing log / meta / trash / project delete ===")
    st, prefs = req("GET", "/api/writing/prefs")
    ok("writing prefs get", str(st)) if st == 200 else fail("misbehave", "writing prefs", f"{st}")
    st, days = req("GET", "/api/writing/days?from=2026-01-01&to=2026-12-31")
    ok("writing days get", str(st)) if st == 200 else fail("misbehave", "writing days", f"{st}")
    st, hb = req("POST", "/api/writing/heartbeat", {"chars": 12, "active": True})
    ok("writing heartbeat", str(st)) if st in (200, 201) else fail("minor", "writing heartbeat", f"{st} {hb}")

    st, formats = req("GET", "/api/meta/export-formats")
    ok("export formats meta") if st == 200 else fail("misbehave", "export formats", f"{st}")
    st, purposes = req("GET", "/api/meta/work-purposes")
    ok("work purposes meta") if st == 200 else fail("misbehave", "work purposes", f"{st}")
    st, ai_status = req("GET", "/api/ai/status")
    ok("ai status (info only)", str(ai_status)[:80] if st == 200 else str(st))

    st, trash = req("POST", f"/api/scenes/{sc2['id']}/trash", {})
    if st == 200:
        ok("trash scene")
        st, tlist = req("GET", f"/api/projects/{pid}/trash")
        ok("list trash", f"count={tlist.get('count') if isinstance(tlist, dict) else '?'}") if st == 200 else fail(
            "misbehave", "list trash", f"{st}"
        )
        st, restored = req("POST", f"/api/scenes/{sc2['id']}/restore", {})
        ok("restore scene") if st == 200 else fail("misbehave", "restore", f"{st}")
    else:
        fail("misbehave", "trash scene", f"{st} {trash}")

    st, stats = req("GET", f"/api/projects/{pid}/manuscript-stats")
    ok("manuscript stats", str(stats)[:80] if st == 200 else f"{st}")

    st, del_char = req("DELETE", f"/api/characters/{char['id']}")
    ok("delete character") if st == 200 else fail("misbehave", "delete character", f"{st}")

    st, del_proj = req("DELETE", f"/api/projects/{pid}")
    if st == 200:
        ok("soft-delete project")
        st, plist = req("GET", "/api/projects")
        still = any(p.get("id") == pid for p in plist) if isinstance(plist, list) else True
        if not still:
            ok("deleted project hidden from list")
        else:
            fail("misbehave", "deleted project still listed", str(plist)[:100])
    else:
        fail("misbehave", "delete project", f"{st} {del_proj}")

    # empty content put
    st, p2 = req("POST", "/api/projects", {"title": "빈 회차 QA", "main_genre": "판타지"})
    st, c2 = req("POST", f"/api/projects/{p2['id']}/chapters", {"title": "장"})
    st, s_empty = req("POST", f"/api/chapters/{c2['id']}/scenes", {"title": "빈화"})
    st, d0 = req("GET", f"/api/scenes/{s_empty['id']}")
    st, se = req(
        "PUT",
        f"/api/scenes/{s_empty['id']}",
        {"title": "빈화", "status": "draft", "content_md": "", "row_version": d0["row_version"]},
    )
    ok("save empty scene content") if st == 200 else fail("misbehave", "empty scene save", f"{st}")

    # bulk scene goals
    st, goals = req(
        "POST",
        f"/api/projects/{p2['id']}/scene-goals",
        {"goal_word_count": 3000, "goal_metric": "chars_with_space"},
    )
    ok("bulk scene goals", str(st)) if st == 200 else fail("minor", "bulk scene goals", f"{st} {goals}")

    print("\n=== I. Cross-check frontend/backend contracts (static) ===")
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    checks = [
        ("DEFAULT baits after characters", 'DEFAULT_SETTINGS_ORDER = ["ideas", "intro", "logsyn", "keywords", "world", "characters", "baits"' in app_js),
        ("bait API refreshBaitsFromServer", "refreshBaitsFromServer" in app_js and "/baits" in app_js),
        ("bait local migrate", "migrateLocalBaitsToDb" in app_js),
        ("settings context only toggle", "settings-box-toggle[data-settings-toggle]" in app_js),
        ("함께보기 label", "함께보기" in index_html and "다른 씬 보기" not in index_html),
        ("sceneCharacterSaveButton", "sceneCharacterSaveButton" in index_html and "persistSceneCharacterLinks" in app_js),
        ("outline_summary field", "outline_summary" in app_js or "outlineSummary" in app_js),
        ("project delete admin", "deleteProjectFromAdmin" in app_js),
        ("character portrait UI", "characterPortraitAddButton" in index_html),
    ]
    for name, good in checks:
        if good:
            ok(f"contract: {name}")
        else:
            fail("misbehave", f"contract: {name}", "frontend/backend mismatch")

    server.shutdown()
    td.cleanup()

    print("\n========== SUMMARY ==========")
    print(f"PASS {PASS}  FAIL {FAIL}")
    for sev, name, detail in NOTES:
        print(f"  [{sev}] {name}: {detail}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)

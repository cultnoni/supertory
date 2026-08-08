"""Edge-case API checks for non-AI SuperTory features."""
from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app

PASS = 0
FAIL = 0
NOTES: list[tuple[str, str, str]] = []


def ok(name: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    print(f"  OK  {name}" + (f" — {detail}" if detail else ""))


def fail(sev: str, name: str, detail: str) -> None:
    global FAIL
    FAIL += 1
    NOTES.append((sev, name, detail))
    print(f"  FAIL[{sev}] {name} — {detail}")


def main() -> int:
    td = tempfile.TemporaryDirectory()
    app.DATA_DIR = Path(td.name) / "data"
    app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
    app.initialise_database()
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = int(server.server_port)

    def req(method: str, path: str, body: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        payload = None
        headers = {}
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, payload, headers)
        resp = conn.getresponse()
        data = resp.read()
        st = resp.status
        conn.close()
        try:
            return st, json.loads(data.decode("utf-8"))
        except Exception:
            return st, data.decode("utf-8", errors="replace")

    print("=== Edge cases ===")

    # Correct writing heartbeat payload (as FE sends)
    st, hb = req(
        "POST",
        "/api/writing/heartbeat",
        {
            "day": str(date.today()),
            "chars_delta": 12,
            "active_seconds_delta": 5,
            "session_start": True,
            "project_id": None,
        },
    )
    if st == 200:
        ok("writing heartbeat with day", str(hb)[:100])
    else:
        fail("misbehave", "writing heartbeat", f"{st} {hb}")

    st, p = req("POST", "/api/projects", {"title": "edge", "main_genre": "판타지"})
    if st != 201:
        fail("crash", "create project", f"{st} {p}")
        server.shutdown()
        td.cleanup()
        return 1
    pid = p["id"]
    ok("create project", f"id={pid}")

    # empty bait quote
    st, bait = req(
        "POST",
        f"/api/projects/{pid}/baits",
        {"kind": "plant", "quote": "", "summary": ""},
    )
    if st in (200, 201):
        ok("empty bait create allowed", f"id={bait.get('id')}")
    elif st == 400:
        ok("empty bait rejected cleanly", str(bait)[:80])
    else:
        fail("crash", "empty bait", f"{st} {bait}")

    # special chars bait
    st, bait2 = req(
        "POST",
        f"/api/projects/{pid}/baits",
        {
            "kind": "plant",
            "quote": "특수<>&\"'…★※",
            "summary": "요약\n줄바꿈",
            "notify_on_recover": True,
        },
    )
    if st in (200, 201) and bait2.get("id"):
        ok("special char bait", bait2.get("quote", "")[:30])
        st, d = req("DELETE", f"/api/baits/{bait2['id']}")
        ok("delete bait") if st == 200 else fail("misbehave", "delete bait", f"{st}")
    else:
        fail("misbehave", "special bait", f"{st} {bait2}")

    # very long outline_summary (server caps 20000)
    long = "줄거리 " * 5000
    st, s = req("PUT", f"/api/projects/{pid}/settings", {"outline_summary": long})
    if st == 200:
        got = len(s.get("outline_summary") or "")
        if got <= 20000:
            ok("long outline_summary capped", f"len={got}")
        else:
            fail("minor", "outline_summary no cap", f"len={got}")
    else:
        fail("misbehave", "long outline_summary", f"{st}")

    # empty outline ops
    st, o = req("GET", f"/api/projects/{pid}/outline")
    ok("empty outline", f"chapters={len(o.get('chapters') or [])}") if st == 200 else fail(
        "crash", "empty outline", f"{st}"
    )

    st, r = req("POST", f"/api/projects/{pid}/chapters/renumber-titles", {})
    if st in (200, 201):
        ok("renumber empty project", str(r)[:80])
    else:
        fail("minor", "renumber empty", f"{st} {r}")

    st, t = req("GET", f"/api/projects/{pid}/trash")
    ok("trash empty list") if st == 200 else fail("misbehave", "trash empty", f"{st}")

    st, idx = req("GET", f"/api/projects/{pid}/index")
    ok("project index empty", f"{st}") if st == 200 else fail("minor", "project index", f"{st} {idx}")

    st, p2 = req("POST", "/api/projects", {"title": "edge2", "main_genre": "로맨스"})
    st, ro = req("POST", "/api/projects/reorder", {"project_ids": [p2["id"], pid]})
    ok("project reorder") if st == 200 else fail("misbehave", "reorder", f"{st} {ro}")

    st, lm = req("POST", "/api/projects/list-mode", {"mode": "recent"})
    ok("list-mode recent") if st == 200 else fail("minor", "list-mode", f"{st} {lm}")

    # export prefs
    st, ep = req("GET", "/api/export/prefs")
    ok("export prefs get") if st == 200 else fail("minor", "export prefs", f"{st}")

    # spellcheck local
    st, sp = req("POST", "/api/spellcheck", {"text": "안녕하세요 테스트입니다."})
    if st == 200:
        ok("spellcheck", str(sp)[:100])
    else:
        fail("minor", "spellcheck", f"{st} {str(sp)[:100]}")

    # scene summary empty
    st, ch = req("POST", f"/api/projects/{pid}/chapters", {"title": "장"})
    st, sc = req("POST", f"/api/chapters/{ch['id']}/scenes", {"title": "화"})
    st, sum0 = req("GET", f"/api/scenes/{sc['id']}/summary")
    ok("scene summary empty get", f"{st}") if st in (200, 404) else fail(
        "minor", "scene summary", f"{st}"
    )
    st, sum1 = req("PUT", f"/api/scenes/{sc['id']}/summary", {"summary_md": "한 줄 요약"})
    ok("scene summary put") if st == 200 else fail("minor", "scene summary put", f"{st} {sum1}")

    # manuscript stats empty-ish
    st, stats = req("GET", f"/api/projects/{pid}/manuscript-stats")
    ok("stats empty project", str(stats)[:80]) if st == 200 else fail("misbehave", "stats", f"{st}")

    # alias
    st, char = req("POST", f"/api/projects/{pid}/characters", {"name": "주인공"})
    if st == 201:
        st, al = req(
            "POST",
            f"/api/characters/{char['id']}/aliases",
            {"alias": "그 아이"},
        )
        ok("character alias", f"{st}") if st in (200, 201) else fail("minor", "alias", f"{st} {al}")

    # part reorder empty-ish
    st, part = req("POST", f"/api/projects/{pid}/parts", {"title": "부"})
    if st in (200, 201):
        st, pr = req(
            "POST",
            f"/api/projects/{pid}/parts/reorder",
            {"part_ids": [part["id"]]},
        )
        ok("part reorder") if st == 200 else fail("minor", "part reorder", f"{st}")

    # soft delete then purge scene
    st, _ = req("POST", f"/api/scenes/{sc['id']}/trash", {})
    st, purged = req("DELETE", f"/api/scenes/{sc['id']}/purge")
    ok("purge trashed scene", f"{st}") if st == 200 else fail("minor", "purge", f"{st} {purged}")

    # settings with only outline_summary
    st, s2 = req(
        "PUT",
        f"/api/projects/{pid}/settings",
        {"outline_summary": "개요만"},
    )
    if st == 200 and s2.get("outline_summary") == "개요만":
        ok("settings partial put outline_summary only")
    else:
        fail("misbehave", "partial settings", f"{st} {s2}")

    # bait with missing recover scene still ok
    st, bait3 = req(
        "POST",
        f"/api/projects/{pid}/baits",
        {
            "kind": "plant",
            "quote": "미회수 떡밥",
            "notify_on_recover": True,
            "recover_scene_id": 999999,
        },
    )
    if st in (200, 201):
        ok("bait with invalid recover_scene still stored or accepted", str(bait3)[:80])
    elif st == 400:
        ok("bait invalid recover_scene rejected cleanly", str(bait3)[:80])
    else:
        fail("crash", "bait invalid recover", f"{st} {bait3}")

    # static files from temp server
    for path in ("/", "/app.js", "/styles.css", "/index.html"):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", path)
        resp = conn.getresponse()
        data = resp.read()
        st = resp.status
        conn.close()
        if st == 200 and len(data) > 100:
            ok(f"static {path}", f"bytes={len(data)}")
        else:
            fail("misbehave", f"static {path}", f"{st} len={len(data)}")

    server.shutdown()
    td.cleanup()

    print("\n========== SUMMARY ==========")
    print(f"PASS {PASS}  FAIL {FAIL}")
    for sev, name, detail in NOTES:
        print(f"  [{sev}] {name}: {detail}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

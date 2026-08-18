"""Sample every virtual-reader persona via live 1:1 chat (project 11).

Checks that replies do not invent 회빙환-style tropes absent from the work's
synopsis / worldbuilding / project_index.

Run:
  python -m unittest tests.test_reader_persona_sampling -v
  python tests/test_reader_persona_sampling.py
"""

from __future__ import annotations

import http.client
import json
import re
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

import app
import gemini_client

ROOT = Path(__file__).resolve().parents[1]
REAL_DB = ROOT / "data" / "supertory.sqlite3"
PROJECT_ID = 11
USER_MESSAGE = "이 작품 어때요?"

# Plain substring hits (must be absent from work settings/index first).
BANNED_LITERALS = (
    "회귀",
    "회빙환",
    "빙의",
    "환생",
    "전생",
    "게임 시스템",
)

# Broader / phrase-style hits. "창" alone is too common (창문·창가…), so only
# game-stat / status-window sense is flagged.
BANNED_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"상태\s*창|스탯\s*창|능력(?:치)?\s*창|시스템\s*창|인벤(?:토리)?\s*창|게임\s*창",
        "창(스탯/능력)",
    ),
    (r"1인칭\s*시점|시점\s*전환", "1인칭 시점 전환"),
)


def flatten_persona_ids(grouped: object) -> list[dict]:
    out: list[dict] = []
    if not isinstance(grouped, dict):
        return out
    for key in app.READER_PERSONA_CATEGORIES:
        people = grouped.get(key) or []
        if not isinstance(people, list):
            continue
        for item in people:
            if isinstance(item, dict) and str(item.get("id") or "").strip():
                out.append(item)
    return out


def find_banned_hits(text: str) -> list[str]:
    hits: list[str] = []
    body = str(text or "")
    for word in BANNED_LITERALS:
        if word in body:
            hits.append(word)
    for pattern, label in BANNED_PATTERNS:
        if re.search(pattern, body):
            hits.append(label)
    # stable unique order
    seen: set[str] = set()
    ordered: list[str] = []
    for item in hits:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def work_settings_blob(connection) -> str:
    row = connection.execute(
        "SELECT description_md, worldbuilding_md FROM project "
        "WHERE id = ? AND deleted_at IS NULL",
        (PROJECT_ID,),
    ).fetchone()
    if row is None:
        return ""
    parts = [str(row["description_md"] or ""), str(row["worldbuilding_md"] or "")]
    try:
        idx = connection.execute(
            "SELECT characters_json, world_rules_json, timeline_json, "
            "open_threads_json, tracked_facts_json "
            "FROM project_index WHERE project_id = ?",
            (PROJECT_ID,),
        ).fetchone()
    except Exception:
        idx = None
    if idx is not None:
        parts.extend(str(idx[key] or "") for key in idx.keys())
    return "\n".join(parts)


def summarize(text: str, limit: int = 100) -> str:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1] + "…"


def print_results_table(rows: list[dict], total: int) -> None:
    lines: list[str] = []
    lines.append(f"=== 가상독자 회빙환 가정 금지 샘플링 (프로젝트 {PROJECT_ID}) ===")
    lines.append(f"전체 {total}명 중 테스트함")
    lines.append("")
    lines.append(
        "| persona_id | persona_name | 키워드 언급 여부 | 언급된 키워드 | 응답 요약(앞 100자) |"
    )
    lines.append("|---|---|---|---|---|")
    for row in rows:
        flag = "YES" if row["hits"] else "no"
        hits = ", ".join(row["hits"]) if row["hits"] else "-"
        name = str(row["persona_name"]).replace("|", "\\|")
        summary = str(row["summary"]).replace("|", "\\|")
        lines.append(
            f"| `{row['persona_id']}` | {name} | {flag} | {hits} | {summary} |"
        )
    flagged = [row for row in rows if row["hits"]]
    lines.append("")
    lines.append(f"키워드 언급: {len(flagged)}/{total}명")
    if not flagged:
        lines.append("전체 통과")
    else:
        lines.append(
            "문제 페르소나: " + ", ".join(f"`{r['persona_id']}`" for r in flagged)
        )
    text = "\n".join(lines) + "\n"
    print(text)
    report = ROOT / "tests" / "_persona_sampling_report.md"
    report.write_text(text, encoding="utf-8")
    print(f"(보고서 저장: {report})")

@unittest.skipUnless(REAL_DB.exists(), "data/supertory.sqlite3 missing")
@unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
class ReaderPersonaSamplingTests(unittest.TestCase):
    """Live scan: every persona vs project 11 without manuscript attach."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._orig_data = app.DATA_DIR
        cls._orig_db = app.DATABASE_PATH
        dst = Path(cls._tmpdir.name) / "data"
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REAL_DB, dst / "supertory.sqlite3")
        for suffix in ("-wal", "-shm"):
            extra = Path(str(REAL_DB) + suffix)
            if extra.exists():
                shutil.copy2(extra, dst / (REAL_DB.name + suffix))
        app.DATA_DIR = dst
        app.DATABASE_PATH = dst / "supertory.sqlite3"
        app.initialise_database()
        with app.database() as connection:
            project = connection.execute(
                "SELECT id, title FROM project WHERE id = ? AND deleted_at IS NULL",
                (PROJECT_ID,),
            ).fetchone()
            if project is None:
                raise unittest.SkipTest(f"project {PROJECT_ID} not found")
            blob = work_settings_blob(connection)
            pre_hits = find_banned_hits(blob)
            if pre_hits:
                raise unittest.SkipTest(
                    f"project {PROJECT_ID} settings/index already contain "
                    f"{pre_hits} — keyword hits would be true positives"
                )
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.gap = float(getattr(app, "IMPORT_ANALYSIS_GEMINI_GAP_SECONDS", 1.8) or 1.8)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()
        app.DATA_DIR = cls._orig_data
        app.DATABASE_PATH = cls._orig_db
        cls._tmpdir.cleanup()

    def request(
        self, method: str, path: str, payload: dict | None = None, timeout: int = 180
    ) -> tuple[int, object]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=timeout
        )
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = raw
        return response.status, data

    def test_no_trope_invention_across_all_personas(self) -> None:
        status, grouped = self.request("GET", "/api/reader-personas")
        self.assertEqual(status, 200, grouped)
        people = flatten_persona_ids(grouped)
        total = len(people)
        self.assertGreater(total, 0, "persona list empty")
        print(f"\n전체 {total}명 중 테스트함 (질문: {USER_MESSAGE!r}, 원고 없음)")

        rows: list[dict] = []
        for index, persona in enumerate(people):
            persona_id = str(persona.get("id") or "").strip()
            persona_name = str(persona.get("name") or "").strip() or persona_id
            if index > 0 and self.gap > 0:
                time.sleep(self.gap)
            status, chat = self.request(
                "POST",
                "/api/reader-chat",
                {
                    "work_id": str(PROJECT_ID),
                    "persona_id": persona_id,
                    "user_message": USER_MESSAGE,
                },
            )
            if status != 200:
                reply = f"[ERROR {status}] {chat}"
                hits = ["(호출 실패)"]
            else:
                reply = str(chat.get("reply") or "")
                hits = find_banned_hits(reply)
            rows.append(
                {
                    "persona_id": persona_id,
                    "persona_name": persona_name,
                    "hits": hits,
                    "summary": summarize(reply),
                    "reply": reply,
                }
            )
            flag = "YES" if hits else "ok"
            print(f"  [{index + 1}/{total}] {persona_id}: {flag}")

        print_results_table(rows, total)

        # Soft assert: collect failures but still print full table above.
        flagged = [row for row in rows if row["hits"] and row["hits"] != ["(호출 실패)"]]
        errors = [row for row in rows if row["hits"] == ["(호출 실패)"]]
        if errors:
            self.fail(
                f"{len(errors)} persona chat call(s) failed: "
                + ", ".join(r["persona_id"] for r in errors)
            )
        if flagged:
            # Report only — do not rewrite prompts in this task.
            detail = "; ".join(
                f"{r['persona_id']}({','.join(r['hits'])})" for r in flagged
            )
            self.fail(
                f"{len(flagged)}/{total} persona(s) mentioned banned tropes: {detail}"
            )


def main() -> None:
    if not REAL_DB.exists():
        raise SystemExit(f"missing {REAL_DB}")
    if not gemini_client.is_configured():
        raise SystemExit("GEMINI_API_KEY not configured")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReaderPersonaSamplingTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()

"""성공 패턴 분석 HTTP API. app.py는 라우트만 연결한다.

참고 작품을 구간별로 읽어 성공 공식 프로필을 만들고 저장·조회·이름 변경·삭제한다.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
from http import HTTPStatus

import gemini_client
import genre_clusters
import success_pattern


def _database():
    import app

    return app.database()


def _as_dict(row):
    import app

    return app.as_dict(row)


def parse_success_pattern_document(handler, body: dict) -> dict:
    filename = str(body.get("filename") or body.get("file_name") or "upload.txt")
    raw_b64 = body.get("content_base64") or body.get("contentBase64") or ""
    if not raw_b64:
        raise ValueError("파일 내용이 없어요.")
    try:
        data = base64.b64decode(str(raw_b64), validate=False)
    except (binascii.Error, ValueError) as error:
        raise ValueError("파일 데이터를 읽지 못했어요.") from error
    split_mode = str(body.get("split_mode") or body.get("splitMode") or "headings")
    episodes = success_pattern.parse_document_to_episodes(
        filename, data, split_mode=split_mode
    )
    total_chars = sum(int(ep.get("length") or 0) for ep in episodes)
    return {
        "ok": True,
        "filename": filename,
        "episode_count": len(episodes),
        "total_chars": total_chars,
        "episodes": episodes,
    }


def check_success_pattern_budget(handler, body: dict) -> dict:
    sections = handler._normalize_uploaded_sections(body)
    episode_total = sum(max(s.uploaded_count, s.episode_count) for s in sections)
    # Prefer actual uploaded episode counts when present
    uploaded_eps = sum(s.uploaded_count for s in sections)
    if uploaded_eps:
        episode_total = uploaded_eps
    ep_budget = success_pattern.check_episode_budget(episode_total)
    char_budget = success_pattern.check_character_budget(sections)
    stats = success_pattern.compute_quantitative_stats(sections)
    status = "ok"
    if ep_budget["status"] == "blocked" or char_budget["status"] == "blocked":
        status = "blocked"
    elif ep_budget["status"] == "warning" or char_budget["status"] == "warning":
        status = "warning"
    return {
        "status": status,
        "episode_budget": ep_budget,
        "character_budget": char_budget,
        "stats": stats,
    }


def run_success_pattern_analysis(handler, body: dict) -> dict:
    work_title = str(body.get("work_title") or body.get("workTitle") or "").strip()
    prompt_cluster_id = genre_clusters.resolve_cluster_id(
        cluster_id=body.get("cluster_id"),
        purpose=body.get("purpose"),
        main_genre=body.get("main_genre"),
        sub_genre=body.get("sub_genre"),
    )
    if not work_title:
        raise ValueError("작품명을 입력해 주세요.")
    try:
        total_chapters = int(body.get("total_chapters") or body.get("totalChapters") or 0)
    except (TypeError, ValueError) as error:
        raise ValueError("총 회차 수가 올바르지 않아요.") from error
    if total_chapters < 1:
        raise ValueError("총 회차 수를 1 이상으로 입력해 주세요.")

    sections = handler._normalize_uploaded_sections(body)
    uploaded_eps = sum(s.uploaded_count for s in sections)
    if uploaded_eps < 1:
        raise ValueError("분석할 회차 본문이 없어요. 구간 파일을 올려 주세요.")

    ep_budget = success_pattern.check_episode_budget(uploaded_eps)
    if ep_budget["status"] == "blocked":
        raise ValueError(ep_budget["message"])
    char_budget = success_pattern.check_character_budget(sections)
    if char_budget["status"] == "blocked":
        raise ValueError(char_budget["message"])

    dry_run = bool(body.get("dry_run") or body.get("dryRun"))
    # Cap per-episode text sent to model
    max_scene_chars = 12000
    chapter_notes: list[dict] = []
    for section in sections:
        for ep in section.episodes:
            content = ep.text or ""
            if len(content) > max_scene_chars:
                content = content[:max_scene_chars] + "\n…(이하 생략)"
            prompt = success_pattern.build_structural_observation_prompt(
                content, cluster_id=prompt_cluster_id
            )
            if dry_run or not gemini_client.is_configured():
                note = success_pattern.mock_observation_note(ep.text or "")
                used_mock = True
            else:
                try:
                    # Task-only: no Tory Core Identity system block.
                    raw = gemini_client.generate_text(
                        prompt,
                        system=None,
                        temperature=0.35,
                        max_output_tokens=1024,
                    )
                    note = success_pattern.extract_json_object(raw)
                    used_mock = False
                except Exception:
                    note = success_pattern.mock_observation_note(ep.text or "")
                    used_mock = True
            chapter_notes.append({
                "section_key": section.key,
                "section_label": section.label,
                "episode_title": ep.title,
                "episode_index": ep.index,
                "char_count": ep.length,
                "observation": note,
                "mock": used_mock,
            })

    quantitative = success_pattern.compute_quantitative_stats(sections)
    merge_prompt = success_pattern.build_success_pattern_merge_prompt(
        quantitative, chapter_notes, cluster_id=prompt_cluster_id
    )
    profile_mock = True
    if dry_run or not gemini_client.is_configured() or any(n.get("mock") for n in chapter_notes):
        profile = success_pattern.mock_merge_profile(quantitative, chapter_notes)
    else:
        try:
            raw_profile = gemini_client.generate_text(
                merge_prompt,
                system=None,
                temperature=0.4,
                max_output_tokens=2048,
            )
            profile = success_pattern.extract_json_object(raw_profile)
            profile_mock = False
        except Exception:
            profile = success_pattern.mock_merge_profile(quantitative, chapter_notes)
            profile_mock = True

    profile = success_pattern.normalize_merge_profile(profile)
    analyzed_sections = [
        {
            "key": s.key,
            "label": s.label,
            "start_ep": s.start_ep,
            "end_ep": s.end_ep,
            "episode_count": s.uploaded_count,
            "char_count": s.char_count,
        }
        for s in sections
    ]

    with _database() as connection:
        cursor = connection.execute(
            "INSERT INTO success_pattern_profile("
            "work_title, total_chapters, analyzed_sections_json, profile_json, quantitative_json"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                work_title[:200],
                total_chapters,
                json.dumps(analyzed_sections, ensure_ascii=False),
                json.dumps(profile, ensure_ascii=False),
                json.dumps(quantitative, ensure_ascii=False),
            ),
        )
        profile_id = int(cursor.lastrowid)
        connection.executemany(
            "INSERT INTO success_pattern_chapter_notes("
            "profile_id, note_order, section_key, section_label, episode_title, "
            "episode_index, char_count, observation_json, used_mock"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    profile_id,
                    note_order,
                    str(note.get("section_key") or ""),
                    str(note.get("section_label") or ""),
                    str(note.get("episode_title") or ""),
                    (
                        int(note["episode_index"])
                        if note.get("episode_index") is not None
                        else None
                    ),
                    max(0, int(note.get("char_count") or 0)),
                    json.dumps(
                        note.get("observation")
                        if isinstance(note.get("observation"), dict)
                        else {},
                        ensure_ascii=False,
                    ),
                    1 if note.get("mock") else 0,
                )
                for note_order, note in enumerate(chapter_notes)
            ],
        )
        row = connection.execute(
            "SELECT id, work_title, total_chapters, analyzed_sections_json, profile_json, "
            "quantitative_json, built_at FROM success_pattern_profile WHERE id = ?",
            (profile_id,),
        ).fetchone()
    return {
        "ok": True,
        "profile": _serialize_success_pattern_row(row),
        "chapter_notes": chapter_notes,
        "used_mock": profile_mock,
        "cluster_id": prompt_cluster_id,
        "episode_budget": ep_budget,
        "character_budget": char_budget,
    }


def _serialize_success_pattern_row(row: sqlite3.Row | dict | None) -> dict:
    if row is None:
        raise ValueError("프로파일을 찾을 수 없습니다.")
    item = _as_dict(row) or {}

    def _loads(key: str, default):
        raw = item.get(key)
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw or ("[]" if isinstance(default, list) else "{}"))
        except (json.JSONDecodeError, TypeError):
            return default

    return {
        "id": int(item.get("id") or 0),
        "work_title": item.get("work_title") or "",
        "total_chapters": item.get("total_chapters"),
        "analyzed_sections": _loads("analyzed_sections_json", []),
        "profile": _loads("profile_json", {}),
        "quantitative": _loads("quantitative_json", {}),
        "built_at": item.get("built_at") or "",
    }


def list_success_pattern_profiles(handler) -> list[dict]:
    with _database() as connection:
        rows = connection.execute(
            "SELECT id, work_title, total_chapters, analyzed_sections_json, profile_json, "
            "quantitative_json, built_at FROM success_pattern_profile "
            "ORDER BY datetime(built_at) DESC, id DESC LIMIT 100"
        ).fetchall()
    return [_serialize_success_pattern_row(r) for r in rows]


def get_success_pattern_profile(handler, profile_id: int) -> dict:
    with _database() as connection:
        row = connection.execute(
            "SELECT id, work_title, total_chapters, analyzed_sections_json, profile_json, "
            "quantitative_json, built_at FROM success_pattern_profile WHERE id = ?",
            (profile_id,),
        ).fetchone()
    return _serialize_success_pattern_row(row)


def rename_success_pattern_profile(handler, profile_id: int, body: dict) -> dict:
    work_title = str(body.get("work_title") or body.get("title") or "").strip()
    if not work_title:
        raise ValueError("프로파일 이름을 입력해 주세요.")
    if len(work_title) > 200:
        raise ValueError("프로파일 이름은 200자 이하로 입력해 주세요.")
    with _database() as connection:
        cursor = connection.execute(
            "UPDATE success_pattern_profile SET work_title = ? WHERE id = ?",
            (work_title, int(profile_id)),
        )
        if cursor.rowcount < 1:
            raise LookupError("프로파일을 찾을 수 없습니다.")
        row = connection.execute(
            "SELECT id, work_title, total_chapters, analyzed_sections_json, profile_json, "
            "quantitative_json, built_at FROM success_pattern_profile WHERE id = ?",
            (int(profile_id),),
        ).fetchone()
    return _serialize_success_pattern_row(row)


def delete_success_pattern_profile(handler, profile_id: int) -> dict:
    with _database() as connection:
        linked_count = int(connection.execute(
            "SELECT COUNT(*) FROM project WHERE linked_success_profile_id = ?",
            (int(profile_id),),
        ).fetchone()[0])
        cursor = connection.execute(
            "DELETE FROM success_pattern_profile WHERE id = ?",
            (int(profile_id),),
        )
        if cursor.rowcount < 1:
            raise LookupError("프로파일을 찾을 수 없습니다.")
    return {
        "ok": True,
        "id": int(profile_id),
        "unlinked_projects": linked_count,
    }


def try_handle_get(handler, path: str) -> bool:
    if path == "/api/success-pattern/profiles":
        handler.send_json(list_success_pattern_profiles(handler))
        return True
    match = re.fullmatch(r"/api/success-pattern/profiles/(\d+)", path)
    if match:
        handler.send_json(get_success_pattern_profile(handler, int(match.group(1))))
        return True
    if path == "/api/success-pattern/meta":
        handler.send_json(
            {
                "max_total_episodes": success_pattern.MAX_TOTAL_EPISODES,
                "recommended_total_episodes": success_pattern.RECOMMENDED_TOTAL_EPISODES,
                "max_total_chars": success_pattern.MAX_TOTAL_CHARS,
                "recommended_total_chars": success_pattern.RECOMMENDED_TOTAL_CHARS,
                "default_window": success_pattern.DEFAULT_WINDOW,
                "section_labels": success_pattern.SECTION_LABELS,
            }
        )
        return True
    return False


def try_handle_post(handler, path: str, body: dict | None) -> bool:
    body = body or {}
    if path == "/api/success-pattern/recommend-ranges":
        total = body.get("total_chapters") or body.get("totalChapters") or 1
        window = body.get("window") or success_pattern.DEFAULT_WINDOW
        handler.send_json(
            {
                "total_chapters": max(1, int(total)),
                "ranges": success_pattern.recommend_ranges(int(total), int(window)),
            }
        )
        return True
    if path == "/api/success-pattern/parse":
        handler.send_json(parse_success_pattern_document(handler, body))
        return True
    if path == "/api/success-pattern/check-budget":
        handler.send_json(check_success_pattern_budget(handler, body))
        return True
    if path == "/api/success-pattern/run":
        handler.send_json(run_success_pattern_analysis(handler, body))
        return True
    return False


def try_handle_put(handler, path: str, body: dict | None) -> bool:
    match = re.fullmatch(r"/api/success-pattern/profiles/(\d+)", path)
    if match is None:
        return False
    handler.send_json(
        rename_success_pattern_profile(handler, int(match.group(1)), body or {})
    )
    return True


def try_handle_delete(handler, path: str) -> bool:
    match = re.fullmatch(r"/api/success-pattern/profiles/(\d+)", path)
    if match is None:
        return False
    handler.send_json(delete_success_pattern_profile(handler, int(match.group(1))))
    return True

"""회차 「이전 버전」: 이력 목록·본문·비교·복원·체크포인트.

원칙(docs/design-ai-context-and-style-2026-11.md §7-1):
- 이력(scene_revision)은 읽기만 한다. 수정·삭제는 트리거가 막는다.
- 복원은 이력을 지우지 않는다. 고른 버전의 내용으로 **새 버전**을 만든다.
- 복원 직전의 현재 버전에는 「되돌리기 전」 체크포인트를 남긴다.
- 체크포인트는 scene_revision_mark(마이그레이션 107)에 따로 기록한다.

save_note에는 사람이 읽는 문구 대신 코드를 남기는 경우가 있다.
화면이 코드를 언어별 문구로 바꾼다: ``@restore:<번호>``.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from contextlib import AbstractContextManager

import sqlite3

from repositories.scene_content_repository import (
    ROW_VERSION_CONFLICT_MESSAGE,
    SceneContentRepository,
)

CHECKPOINT_REASONS = (
    "manual",
    "before_restore",
    "before_ai_insert",
    "before_replace_all",
    "before_complete",
    "before_overwrite",
)
CHECKPOINT_LABEL_MAX = 60
LIST_LIMIT_MAX = 500
RESTORE_NOTE_PREFIX = "@restore:"

RESTORE_LOCKED_MESSAGE = (
    "완성 회차는 잠겨 있어요. 회차 상태를 바꾼 뒤 되돌려 주세요."
)
RESTORE_SAME_MESSAGE = "이미 지금 원고와 같은 버전이에요."
REVISION_NOT_FOUND_MESSAGE = "그 버전을 찾을 수 없습니다."
SCENE_NOT_FOUND_MESSAGE = "회차를 찾을 수 없습니다."

# 문단 비교 상한: 이보다 긴 원고는 비교를 줄여서 보여 준다(느려지지 않게).
DIFF_MAX_LINES = 6000
# 한 문단 안에서 낱말 단위로 보여 줄지 정하는 유사도 기준.
INLINE_DIFF_MIN_RATIO = 0.35

_TOKEN_RE = re.compile(r"\s+|[^\s]+")


def _lines(text: str) -> list[str]:
    """Paragraph lines as the reader sees them (blank lines dropped)."""
    out: list[str] = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if line:
            out.append(line)
    return out


def _inline_parts(old: str, new: str) -> list[dict] | None:
    """Word-level parts for one changed paragraph, or None if too different."""
    a = _TOKEN_RE.findall(old)
    b = _TOKEN_RE.findall(new)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if matcher.ratio() < INLINE_DIFF_MIN_RATIO:
        return None
    parts: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            parts.append({"op": "equal", "text": "".join(a[i1:i2])})
            continue
        if i2 > i1:
            parts.append({"op": "delete", "text": "".join(a[i1:i2])})
        if j2 > j1:
            parts.append({"op": "insert", "text": "".join(b[j1:j2])})
    return parts


def diff_texts(old_text: str, new_text: str) -> dict:
    """Compare two plain texts paragraph by paragraph (then word by word).

    ``ops`` items:
    - ``{"type": "equal", "lines": [...]}``
    - ``{"type": "delete", "lines": [...]}`` — only in the old text
    - ``{"type": "insert", "lines": [...]}`` — only in the new text
    - ``{"type": "change", "parts": [{"op", "text"}]}`` — one paragraph edited
    """
    a = _lines(old_text)
    b = _lines(new_text)
    truncated = False
    if len(a) > DIFF_MAX_LINES or len(b) > DIFF_MAX_LINES:
        a = a[:DIFF_MAX_LINES]
        b = b[:DIFF_MAX_LINES]
        truncated = True
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ops: list[dict] = []
    added = removed = changed = 0

    def push(kind: str, lines: list[str]) -> None:
        if not lines:
            return
        if ops and ops[-1]["type"] == kind and "lines" in ops[-1]:
            ops[-1]["lines"].extend(lines)
        else:
            ops.append({"type": kind, "lines": list(lines)})

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            push("equal", a[i1:i2])
        elif tag == "delete":
            push("delete", a[i1:i2])
            removed += sum(len(x) for x in a[i1:i2])
        elif tag == "insert":
            push("insert", b[j1:j2])
            added += sum(len(x) for x in b[j1:j2])
        else:
            old_block = a[i1:i2]
            new_block = b[j1:j2]
            pairs = min(len(old_block), len(new_block))
            for k in range(pairs):
                parts = _inline_parts(old_block[k], new_block[k])
                if parts is None:
                    push("delete", [old_block[k]])
                    push("insert", [new_block[k]])
                    removed += len(old_block[k])
                    added += len(new_block[k])
                    continue
                ops.append({"type": "change", "parts": parts})
                changed += 1
                for part in parts:
                    if part["op"] == "delete":
                        removed += len(part["text"])
                    elif part["op"] == "insert":
                        added += len(part["text"])
            if len(old_block) > pairs:
                push("delete", old_block[pairs:])
                removed += sum(len(x) for x in old_block[pairs:])
            if len(new_block) > pairs:
                push("insert", new_block[pairs:])
                added += sum(len(x) for x in new_block[pairs:])
    return {
        "ops": ops,
        "stats": {
            "added_chars": added,
            "removed_chars": removed,
            "changed_paragraphs": changed,
            "identical": not any(op["type"] != "equal" for op in ops),
        },
        "truncated": truncated,
    }


class SceneRevisionService:
    """Read history, compare, restore (as a new version), and mark checkpoints."""

    def __init__(
        self,
        *,
        database: Callable[[], AbstractContextManager[sqlite3.Connection]],
        plain_text: Callable[[str], str],
        restore_writer: Callable[[int, str, int, str], dict],
        repository_factory: Callable[
            [sqlite3.Connection], SceneContentRepository
        ] = SceneContentRepository,
    ) -> None:
        self.database = database
        self.plain_text = plain_text
        self.restore_writer = restore_writer
        self.repository_factory = repository_factory

    # —— 읽기 ——

    @staticmethod
    def _marks_by_revision(marks: list[dict]) -> dict[int, list[dict]]:
        grouped: dict[int, list[dict]] = {}
        for mark in marks:
            grouped.setdefault(int(mark["revision_no"]), []).append(
                {"reason": str(mark["reason"]), "label": str(mark.get("label") or "")}
            )
        return grouped

    def _scene(self, repo: SceneContentRepository, scene_id: int) -> dict:
        scene = repo.get_scene_meta(scene_id)
        if scene is None:
            raise ValueError(SCENE_NOT_FOUND_MESSAGE)
        return scene

    def list_revisions(self, scene_id: int, *, limit: int = 200, offset: int = 0) -> dict:
        limit = max(1, min(int(limit or 200), LIST_LIMIT_MAX))
        offset = max(0, int(offset or 0))
        with self.database() as conn:
            repo = self.repository_factory(conn)
            scene = self._scene(repo, scene_id)
            rows, total = repo.list_revisions(scene_id, limit=limit, offset=offset)
            marks = self._marks_by_revision(repo.list_marks(scene_id))
            tail_before = (
                repo.word_count_before(scene_id, int(rows[-1]["revision_no"]))
                if rows
                else None
            )
        revisions: list[dict] = []
        for index, row in enumerate(rows):
            words = int(row["word_count"] or 0)
            if index + 1 < len(rows):
                previous = int(rows[index + 1]["word_count"] or 0)
            else:
                previous = tail_before
            revision_no = int(row["revision_no"])
            revisions.append(
                {
                    "revision_no": revision_no,
                    "created_at": str(row["created_at"] or ""),
                    "word_count": words,
                    "delta": None if previous is None else words - previous,
                    "save_note": str(row["save_note"] or ""),
                    "is_current": bool(row["is_current"]),
                    "marks": marks.get(revision_no, []),
                }
            )
        return {
            "ok": True,
            "scene_id": int(scene_id),
            "status": str(scene.get("status") or ""),
            "row_version": int(scene.get("row_version") or 0),
            "total": total,
            "limit": limit,
            "offset": offset,
            "revisions": revisions,
        }

    def get_revision(self, scene_id: int, revision_no: int) -> dict:
        with self.database() as conn:
            repo = self.repository_factory(conn)
            self._scene(repo, scene_id)
            row = repo.get_revision(scene_id, revision_no)
            if row is None:
                raise ValueError(REVISION_NOT_FOUND_MESSAGE)
            marks = self._marks_by_revision(repo.list_marks(scene_id))
        return {
            "ok": True,
            "revision_no": int(row["revision_no"]),
            "created_at": str(row["created_at"] or ""),
            "word_count": int(row["word_count"] or 0),
            "save_note": str(row["save_note"] or ""),
            "is_current": bool(row["is_current"]),
            "plain_text": self.plain_text(str(row["content_md"] or "")),
            "marks": marks.get(int(row["revision_no"]), []),
        }

    def diff_with_current(self, scene_id: int, revision_no: int) -> dict:
        """What changed from the chosen version to the manuscript as it is now."""
        with self.database() as conn:
            repo = self.repository_factory(conn)
            self._scene(repo, scene_id)
            old = repo.get_revision(scene_id, revision_no)
            if old is None:
                raise ValueError(REVISION_NOT_FOUND_MESSAGE)
            current = repo.get_current_revision(scene_id)
            if current is None:
                raise ValueError(REVISION_NOT_FOUND_MESSAGE)
        result = diff_texts(
            self.plain_text(str(old["content_md"] or "")),
            self.plain_text(str(current["content_md"] or "")),
        )
        return {
            "ok": True,
            "from_revision": int(old["revision_no"]),
            "to_revision": int(current["revision_no"]),
            **result,
        }

    # —— 바꾸기 ——

    def restore(self, scene_id: int, revision_no: int, row_version: int) -> dict:
        """Write the chosen version as a new current version. History stays."""
        expected = int(row_version or 0)
        with self.database() as conn:
            repo = self.repository_factory(conn)
            scene = self._scene(repo, scene_id)
            if str(scene.get("status") or "") == "complete":
                raise ValueError(RESTORE_LOCKED_MESSAGE)
            if expected and int(scene["row_version"]) != expected:
                raise ValueError(ROW_VERSION_CONFLICT_MESSAGE)
            target = repo.get_revision(scene_id, revision_no)
            if target is None:
                raise ValueError(REVISION_NOT_FOUND_MESSAGE)
            content = str(target["content_md"] or "")
            current = repo.get_current_revision(scene_id)
            if bool(target["is_current"]) or (
                current is not None and str(current["content_md"] or "") == content
            ):
                raise ValueError(RESTORE_SAME_MESSAGE)
            # 되돌리기 직전 상태를 표시해 둔다(빈 원고도 표시: 비어 있던 것도 되돌릴 수 있게).
            repo.mark_current_before_change(
                scene_id, "before_restore", next_content=content, skip_blank=False
            )
        saved = self.restore_writer(
            int(scene_id), content, expected, f"{RESTORE_NOTE_PREFIX}{int(revision_no)}"
        )
        return {
            "ok": True,
            "restored_from": int(revision_no),
            "row_version": int(saved.get("row_version") or 0),
            "revision_no": int(saved.get("revision_no") or 0),
            "word_count": int(saved.get("word_count") or 0),
            "content_md": content,
        }

    def set_checkpoint(
        self,
        scene_id: int,
        *,
        revision_no: int | None = None,
        reason: str = "manual",
        label: str = "",
        checkpoint: bool = True,
    ) -> dict:
        """Mark (or unmark) a version. Without ``revision_no`` the current one."""
        reason = str(reason or "manual").strip()
        if reason not in CHECKPOINT_REASONS:
            raise ValueError("알 수 없는 체크포인트 종류입니다.")
        label = re.sub(r"\s+", " ", str(label or "")).strip()[:CHECKPOINT_LABEL_MAX]
        with self.database() as conn:
            repo = self.repository_factory(conn)
            self._scene(repo, scene_id)
            if revision_no is None:
                current = repo.get_current_revision(scene_id)
                if current is None:
                    raise ValueError(REVISION_NOT_FOUND_MESSAGE)
                target_no = int(current["revision_no"])
            else:
                if repo.get_revision(scene_id, revision_no) is None:
                    raise ValueError(REVISION_NOT_FOUND_MESSAGE)
                target_no = int(revision_no)
            if checkpoint:
                repo.add_mark(scene_id, target_no, reason, label)
            else:
                # 별표 끄기는 그 버전의 표시를 모두 지운다(이력 자체는 남는다).
                repo.remove_marks(scene_id, target_no)
            marks = self._marks_by_revision(repo.list_marks(scene_id))
        return {"ok": True, "revision_no": target_no, "marks": marks.get(target_no, [])}

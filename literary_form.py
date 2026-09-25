"""Literary-track gate for 일반문학 / 순문학.

Later pipeline stages should branch only through ``literary_track``.
Essay and every other cluster stay off this track.

The new-project picker stores the UI labels 일반문학 / 순문학 as
``main_genre`` ``general_lit`` / ``literary`` (cluster ``general_literature``).
``sub_genre`` on those works is a separate length tag (``mid``, ``long``, …),
not the category name, so the track looks at ``main_genre``.
"""

from __future__ import annotations

LITERARY_CLUSTER = "general_literature"
LITERARY_TRACK_MAINS = frozenset({"general_lit", "literary"})
# Accepted if a caller still sends the UI label in sub_genre.
LITERARY_TRACK_SUB_LABELS = frozenset({"일반문학", "순문학", "general_lit", "literary"})
LITERARY_FORMS = frozenset({"short", "long"})
LITERARY_SHORT_IMPORT_CHAR_LIMIT = 60_000
FORM_REQUIRED_MESSAGE = "단편 또는 장편을 선택해 주세요."

STYLE_FIELDS = (
    "style_narration",
    "style_sentence",
    "style_dialogue",
    "style_lexicon",
    "style_choice",
    "style_habit",
)
STYLE_TORI_FIELDS = frozenset({
    "style_narration",
    "style_sentence",
    "style_dialogue",
    "style_lexicon",
})
STYLE_AUTHOR_FIELDS = frozenset({"style_choice", "style_habit"})
STYLE_FIELD_MAX_CHARS = 8000
CONTEST_NAME_MAX = 200
CONTEST_KEYS = (
    "contest_prep",
    "contest_name",
    "contest_pages_min",
    "contest_pages_max",
)
PUBLIC_LITERARY_KEYS = (
    "literary_form",
    "literary_finale_kind",
    "literary_finale_id",
) + STYLE_FIELDS + CONTEST_KEYS


def _text(value: object) -> str:
    return str(value or "").strip()


def _lookup(project: object, key: str) -> object:
    if project is None:
        return None
    if isinstance(project, dict):
        return project.get(key)
    keys = getattr(project, "keys", None)
    if callable(keys):
        try:
            if key in project.keys():
                return project[key]
        except (KeyError, TypeError, IndexError):
            return None
    return getattr(project, key, None)


def is_literary_track_target(
    cluster_id: object = "",
    sub_genre: object = "",
    main_genre: object = "",
) -> bool:
    """True only for 일반문학 and 순문학. Essay is not on the track."""
    if _text(cluster_id) != LITERARY_CLUSTER:
        return False
    main = _text(main_genre)
    sub = _text(sub_genre)
    if main == "essay":
        return False
    if main in LITERARY_TRACK_MAINS:
        return True
    return sub in LITERARY_TRACK_SUB_LABELS


def normalize_literary_form(value: object) -> str | None:
    key = _text(value).lower()
    if key in {"short", "단편"}:
        return "short"
    if key in {"long", "장편"}:
        return "long"
    return None


def literary_track(project: object) -> str | None:
    """Return ``short``, ``long``, or None.

    None means later literary-pipeline stages must not run. That includes
    essay, other clusters, and 일반문학/순문학 works that have not chosen a form yet.
    """
    if not is_literary_track_target(
        _lookup(project, "cluster_id"),
        _lookup(project, "sub_genre"),
        _lookup(project, "main_genre"),
    ):
        return None
    return normalize_literary_form(_lookup(project, "literary_form"))


def resolve_required_literary_form(
    *,
    cluster_id: object,
    main_genre: object,
    sub_genre: object,
    raw: object,
) -> str | None:
    """Create / new-project import. Non-targets always store NULL."""
    if not is_literary_track_target(cluster_id, sub_genre, main_genre):
        return None
    form = normalize_literary_form(raw)
    if form is None:
        raise ValueError(FORM_REQUIRED_MESSAGE)
    return form


def resolve_settings_literary_form(
    *,
    was_target: bool,
    target: bool,
    stored_form: object,
    body: dict,
) -> str | None:
    """Settings save.

    Non-targets always store NULL. Entering the track requires a form.
    Works already on the track may keep a NULL form (legacy) until the writer picks one.
    """
    if not target:
        return None
    if "literary_form" in body:
        form = normalize_literary_form(body.get("literary_form"))
        if form is None:
            raise ValueError(FORM_REQUIRED_MESSAGE)
        return form
    if not was_target:
        raise ValueError(FORM_REQUIRED_MESSAGE)
    return normalize_literary_form(stored_form)


def on_literary_form_changed(
    previous: str | None,
    current: str | None,
    *,
    connection=None,
    project_id: int | None = None,
) -> list[int]:
    """단편을 장편으로 바꾸면 없거나 낡은 요약을 재생성 대기열에 넣는다.

    장편을 단편으로 바꿔도 모티프·복선 데이터는 지우지 않는다. 화면에서만 숨긴다.
    """
    if connection is None or project_id is None:
        return []
    if previous == "short" and current == "long":
        from feedback_pipeline.literature_summary import queue_project_summaries

        return queue_project_summaries(connection, int(project_id))
    return []


def _optional_page(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = int(text)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def contest_fields_from(project: object) -> dict:
    prep = _lookup(project, "contest_prep")
    try:
        enabled = 1 if int(prep or 0) else 0
    except (TypeError, ValueError):
        enabled = 1 if _text(prep).lower() in {"1", "true", "yes", "on"} else 0
    return {
        "contest_prep": enabled,
        "contest_name": _text(_lookup(project, "contest_name"))[:CONTEST_NAME_MAX],
        "contest_pages_min": _optional_page(_lookup(project, "contest_pages_min")),
        "contest_pages_max": _optional_page(_lookup(project, "contest_pages_max")),
    }


def public_literary_fields(project: object) -> dict:
    """Fields to add to an API payload. Empty for essay and other clusters."""
    if not is_literary_track_target(
        _lookup(project, "cluster_id"),
        _lookup(project, "sub_genre"),
        _lookup(project, "main_genre"),
    ):
        return {}
    fields: dict = {"literary_form": literary_track(project)}
    kind = str(_lookup(project, "literary_finale_kind") or "").strip()
    if kind not in {"chapter", "scene"}:
        kind = ""
    try:
        finale_id = int(_lookup(project, "literary_finale_id") or 0)
    except (TypeError, ValueError):
        finale_id = 0
    fields["literary_finale_kind"] = kind
    fields["literary_finale_id"] = finale_id if kind else 0
    for key in STYLE_FIELDS:
        fields[key] = _text(_lookup(project, key))[:STYLE_FIELD_MAX_CHARS]
    fields.update(contest_fields_from(project))
    return fields


def strip_literary_keys(item: dict) -> dict:
    if isinstance(item, dict):
        for key in PUBLIC_LITERARY_KEYS:
            item.pop(key, None)
    return item

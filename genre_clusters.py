"""Genre cluster catalog, reverse-mapping, and feature gating.

Single source for cluster ids / hidden features. UI copy lives in locales;
this module is the contract used by app.py and tests.
"""

from __future__ import annotations

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
CLUSTERS_JSON_PATH = ROOT / "web" / "genre_clusters.json"

ACTIVE_CLUSTER_IDS = (
    "webnovel",
    "genre_literature",
    "general_literature",
    "fairytale",
)
LOCKED_CLUSTER_ID = "locked"
KNOWN_CLUSTER_IDS = ACTIVE_CLUSTER_IDS + (LOCKED_CLUSTER_ID,)

LOCKED_PURPOSES = frozenset({
    "short_story",
    "translation",
    "nonfiction",
    "paper",
    "autobiography",
    "poetry",
    "script",
    "diary",
    "report",
    "column",
    "other",
})

GENRE_LITERATURE_MAIN = frozenset({"mystery", "thriller", "genre_lit", "sf"})
GENRE_LITERATURE_SUB = frozenset({
    "honkaku", "social", "cozy", "legal", "crime",
    "psycho", "action", "horror", "suspense",
    "detective",
    "space", "dystopia", "cyberpunk", "timeslip", "postapo",
})

# Features that can be hidden per cluster. Anything not listed stays visible.
ALL_CLUSTER_FEATURE_IDS = (
    "baits",
    "success_profile",
    "summarize",
    "foreshadow",
    "plottwist",
    "temphook",
    "worldscan",
    "successfeedback",
    "worlddesc",
    "successpattern",
    "character_chat",
    "character_sim",
    "reader_debate",
    "reader_comments",
)

CLUSTER_HIDDEN_FEATURES: dict[str, frozenset[str]] = {
    "webnovel": frozenset(),
    "genre_literature": frozenset(),
    "locked": frozenset(),
    "general_literature": frozenset({
        "baits",
        "success_profile",
        "summarize",
        "foreshadow",
        "plottwist",
        "temphook",
        "worldscan",
        "successfeedback",
        "worlddesc",
        "successpattern",
        "character_chat",
        "character_sim",
        "reader_debate",
        "reader_comments",
    }),
    "fairytale": frozenset({
        "success_profile",
        "foreshadow",
        "plottwist",
        "temphook",
        "worldscan",
        "successfeedback",
        "successpattern",
        "reader_debate",
    }),
}

# (main_genre, sub_genre) → allowed genre_detail keys ("" = none).
GENRE_DETAIL_OPTIONS: dict[tuple[str, str], frozenset[str]] = {
    ("romance", "modern"): frozenset({"", "historical"}),
    ("romance", "romfant"): frozenset({"", "oriental_romfant"}),
    ("fantasy", "male"): frozenset({"", "alt_history", "murim", "urban", "hidden_world", "traditional"}),
    ("fantasy", "female"): frozenset({""}),
}

GENRE_DETAIL_LABELS: dict[str, str] = {
    "": "",
    "historical": "사극",
    "oriental_romfant": "동양로판",
    "alt_history": "대체역사",
    "murim": "무협",
    "urban": "현대판타지",
    "hidden_world": "어반판타지",
    "traditional": "정통판타지",
}


def allowed_genre_details(main_genre: object = "", sub_genre: object = "") -> frozenset[str]:
    main_key = str(main_genre or "").strip()
    sub_key = str(sub_genre or "").strip()
    return GENRE_DETAIL_OPTIONS.get((main_key, sub_key), frozenset({""}))


def normalize_genre_detail(
    main_genre: object = "",
    sub_genre: object = "",
    genre_detail: object = "",
) -> str:
    """Keep genre_detail only when it is allowed for this main+sub pair; else ''."""
    key = str(genre_detail or "").strip()
    allowed = allowed_genre_details(main_genre, sub_genre)
    return key if key in allowed else ""


def genre_detail_label(value: object = "") -> str:
    """Korean label for a stored genre_detail key. Empty key → empty (hide in UI/prompt)."""
    key = str(value or "").strip()
    if not key:
        return ""
    return GENRE_DETAIL_LABELS.get(key, "")


# cluster sub key → (purpose, main_genre, sub_genre)
CLUSTER_SUBGENRE_MAP: dict[str, dict[str, tuple[str, str, str]]] = {
    "webnovel": {
        "romance": ("web_novel", "romance", "modern"),
        "romfant": ("web_novel", "romance", "romfant"),
        "female_fantasy": ("web_novel", "fantasy", "female"),
        "male_fantasy": ("web_novel", "fantasy", "male"),
    },
    "genre_literature": {
        "mystery_detective": ("general_novel", "mystery", "honkaku"),
        "thriller": ("general_novel", "thriller", "psycho"),
        "sf": ("general_novel", "sf", "space"),
    },
    "general_literature": {
        "general_novel": ("general_novel", "contemporary", "daily"),
        "general_lit": ("general_novel", "literary", "mid"),
        "literary": ("general_novel", "literary", "long"),
        "essay": ("essay", "other", "tbd"),
    },
    "fairytale": {
        "fairytale": ("fairy_tale", "", ""),
        "preschool": ("fairy_tale", "preschool", ""),
        "elementary": ("fairy_tale", "elementary", ""),
    },
}

_clusters_cache: list[dict] | None = None


def load_clusters() -> list[dict]:
    global _clusters_cache
    if _clusters_cache is None:
        payload = json.loads(CLUSTERS_JSON_PATH.read_text(encoding="utf-8"))
        _clusters_cache = list(payload.get("clusters") or [])
    return _clusters_cache


def normalize_cluster_id(value: object) -> str:
    key = str(value or "").strip()
    return key if key in KNOWN_CLUSTER_IDS else ""


def normalize_purpose_key(purpose: object) -> str:
    key = str(purpose or "").strip()
    if key == "novel":
        return "general_novel"
    return key or "general_novel"


def infer_cluster_id(
    purpose: object = "",
    main_genre: object = "",
    sub_genre: object = "",
    stored: object = "",
) -> str:
    """Prefer stored cluster_id; otherwise reverse-map from purpose + genre."""
    stored_id = normalize_cluster_id(stored)
    if stored_id:
        return stored_id
    purpose_key = normalize_purpose_key(purpose)
    main_key = str(main_genre or "").strip()
    sub_key = str(sub_genre or "").strip()
    if purpose_key == "web_novel":
        return "webnovel"
    if purpose_key == "fairy_tale":
        return "fairytale"
    if purpose_key == "essay":
        return "general_literature"
    if purpose_key == "general_novel":
        if main_key in GENRE_LITERATURE_MAIN or sub_key in GENRE_LITERATURE_SUB:
            return "genre_literature"
        return "general_literature"
    if purpose_key in LOCKED_PURPOSES:
        return LOCKED_CLUSTER_ID
    return "webnovel"


def resolve_cluster_id(
    *,
    cluster_id: object = "",
    purpose: object = "",
    main_genre: object = "",
    sub_genre: object = "",
) -> str:
    requested = normalize_cluster_id(cluster_id)
    if requested:
        return requested
    return infer_cluster_id(purpose, main_genre, sub_genre)


CLUSTER_SUBGENRE_ALIASES: dict[str, dict[str, str]] = {
    "genre_literature": {
        "detective": "mystery_detective",
        "mystery": "mystery_detective",
    },
}


def map_cluster_subgenre(cluster_id: object, sub_key: object) -> tuple[str, str, str] | None:
    cluster = normalize_cluster_id(cluster_id)
    key = str(sub_key or "").strip()
    mapping = CLUSTER_SUBGENRE_MAP.get(cluster) or {}
    key = (CLUSTER_SUBGENRE_ALIASES.get(cluster) or {}).get(key, key)
    return mapping.get(key)


def hidden_features(cluster_id: object) -> frozenset[str]:
    key = normalize_cluster_id(cluster_id) or infer_cluster_id()
    return CLUSTER_HIDDEN_FEATURES.get(key, frozenset())


def get_visible_features(cluster_id: object) -> list[str]:
    hidden = hidden_features(cluster_id)
    return [feature_id for feature_id in ALL_CLUSTER_FEATURE_IDS if feature_id not in hidden]


def is_feature_visible(feature_id: object, cluster_id: object) -> bool:
    key = str(feature_id or "").strip()
    if not key:
        return True
    return key not in hidden_features(cluster_id)

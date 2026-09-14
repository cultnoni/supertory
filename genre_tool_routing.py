"""Genre-literature 06–09 Tory panel toolset routing (interface only).

Actual tool switching / romance validators ship in a follow-up. This module
exposes a stable plan object that UI and assist endpoints can call now.
"""

from __future__ import annotations

from typing import Any

import genre_clusters


# Placeholder toolset ids for future 06–09 panel wiring.
MAIN_TOOLSET_BY_GENRE: dict[str, str] = {
    "sf": "genre_lit_sf",
    "mystery": "genre_lit_mystery",
    "thriller": "genre_lit_thriller",
    "traditional": "genre_lit_traditional",
    "experimental": "genre_lit_experimental",
    "romance": "genre_lit_romance",
}

ROMANCE_STRUCTURE_TOOLSET: dict[str, str] = {
    "emotional": "genre_lit_romance_emotional",
    "plot_driven": "genre_lit_romance_plot_driven",
}


def _main_toolset_id(main_genre: str) -> str:
    key = str(main_genre or "").strip().lower()
    return MAIN_TOOLSET_BY_GENRE.get(key, f"genre_lit_{key}" if key else "")


def _romance_toolset_id(romance_structure: str, *, blend: str = "") -> str:
    key = genre_clusters.normalize_romance_structure(romance_structure)
    if key:
        return ROMANCE_STRUCTURE_TOOLSET[key]
    # subplot: no structure axis — use the whole romance auxiliary family at low weight.
    # co_axis/main_axis with empty structure: keep family bucket until UI re-entry.
    if blend == "subplot":
        return MAIN_TOOLSET_BY_GENRE["romance"]
    return MAIN_TOOLSET_BY_GENRE["romance"]


def resolve_genre_tool_routing(
    *,
    cluster_id: object = "",
    purpose: object = "",
    main_genre: object = "",
    sub_genre: object = "",
    romance_structure: object = "",
    romance_setting: object = "",
    romance_blend: object = "",
) -> dict[str, Any]:
    """Build a routing plan for genre-literature assist panels (06–09).

    Blend semantics (hooks only — tool lists are TODO):
      none      → main genre toolset only
      subplot   → main primary + romance secondary (low priority; no structure split)
      co_axis   → main + romance(structure) equal weight
      main_axis → romance(structure) primary + main as auxiliary

    When main genre is romance, structure axis is the primary toolset key;
    romance_blend is forced to none.

    romance_structure is consulted whenever main=romance OR blend in
    {co_axis, main_axis}. subplot intentionally ignores structure.
    """
    fields = genre_clusters.coerce_romance_fields_for_project(
        cluster_id=cluster_id,
        purpose=purpose,
        main_genre=main_genre,
        sub_genre=sub_genre,
        romance_structure=romance_structure,
        romance_setting=romance_setting,
        romance_blend=romance_blend,
    )
    structure = fields["romance_structure"]
    setting = fields["romance_setting"]
    blend = fields["romance_blend"]
    is_lit = genre_clusters.is_genre_literature_cluster(
        cluster_id, purpose, main_genre, sub_genre
    )
    is_romance = genre_clusters.is_genre_literature_romance(
        cluster_id=cluster_id,
        purpose=purpose,
        main_genre=main_genre,
        sub_genre=sub_genre,
    )
    period_module = genre_clusters.is_period_support_module_enabled(
        cluster_id=cluster_id,
        purpose=purpose,
        main_genre=main_genre,
        sub_genre=sub_genre,
        romance_setting=setting,
    )

    plan: dict[str, Any] = {
        "cluster_id": genre_clusters.resolve_cluster_id(
            cluster_id=cluster_id,
            purpose=purpose,
            main_genre=main_genre,
            sub_genre=sub_genre,
        ),
        "main_genre": str(main_genre or "").strip().lower(),
        "romance_structure": structure,
        "romance_setting": setting,
        "romance_blend": blend,
        "structure_applies": genre_clusters.romance_structure_applies(
            cluster_id=cluster_id,
            purpose=purpose,
            main_genre=main_genre,
            sub_genre=sub_genre,
            romance_blend=blend,
        ),
        "setting_applies": genre_clusters.romance_setting_applies(
            cluster_id=cluster_id,
            purpose=purpose,
            main_genre=main_genre,
            sub_genre=sub_genre,
        ),
        "period_support_module": period_module,
        "primary_toolset": "",
        "secondary_toolsets": [],
        "auxiliary_toolsets": [],
        "empty_toolset_policy": "keep_visible_with_placeholder",
        # TODO(follow-up): map toolset ids → concrete 06–09 assist mode lists
        # (감정선 몰입도 검사기, SF 검증 도구 등). Until then callers must not
        # hide panels solely because a toolset catalog is empty.
        "todo": (
            "Wire 06–09 panel tool catalogs per toolset id; "
            "SF/mystery/… specialty validators are not designed yet — "
            "when blend is co_axis/main_axis and a specialty toolset is empty, "
            "show a placeholder ('준비 중') rather than dropping the main genre row. "
            "subplot: keep romance auxiliary as a single low-weight family set "
            "(no emotional/plot_driven split)."
        ),
    }

    if not is_lit:
        # Webnovel and other clusters: no genre-lit tool routing.
        return plan

    main_set = _main_toolset_id(plan["main_genre"])
    romance_set = _romance_toolset_id(structure, blend=blend)

    if is_romance:
        plan["primary_toolset"] = romance_set
        plan["secondary_toolsets"] = []
        plan["auxiliary_toolsets"] = []
        return plan

    if blend == "none":
        plan["primary_toolset"] = main_set
    elif blend == "subplot":
        plan["primary_toolset"] = main_set
        plan["secondary_toolsets"] = [romance_set] if romance_set else []
        plan["blend_weight"] = "low"
    elif blend == "co_axis":
        plan["primary_toolset"] = main_set
        plan["secondary_toolsets"] = [romance_set] if romance_set else []
        # Equal weight: secondary is not demoted; UI may interleave.
        plan["blend_weight"] = "equal"
    elif blend == "main_axis":
        plan["primary_toolset"] = romance_set
        plan["auxiliary_toolsets"] = [main_set] if main_set else []
    else:
        plan["primary_toolset"] = main_set

    return plan

"""Cluster-specific Tory prompt pipelines.

Webnovel and genre_literature task prompts are physically separate so they can
be tuned independently. Core Identity / Dynamic Context stay shared.
Until the genre-literature tuning pass, the two pipelines are identical copies.
"""

from __future__ import annotations

GENRE_LITERATURE_CLUSTER = "genre_literature"
WEBNOVEL_PIPELINE = "webnovel"
# Reserved for a later literary helper pass. prompt_pipeline_id() does not use it yet,
# so 일반문학/순문학 assistants keep the webnovel prompts.
GENERAL_LITERATURE_PIPELINE = "general_literature"


def prompt_pipeline_id(cluster_id: object = "") -> str:
    """Return which task-prompt pipeline to use. Non-genre-lit clusters share webnovel."""
    key = str(cluster_id or "").strip()
    return GENRE_LITERATURE_CLUSTER if key == GENRE_LITERATURE_CLUSTER else WEBNOVEL_PIPELINE


def is_genre_literature_pipeline(cluster_id: object = "") -> bool:
    return prompt_pipeline_id(cluster_id) == GENRE_LITERATURE_CLUSTER

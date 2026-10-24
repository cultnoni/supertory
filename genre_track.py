"""장르문학 첨삭 파이프라인의 묶음 판별.

장르문학(``cluster_id=genre_literature``) 하위 장르를 성격이 비슷한 네 묶음으로 나눈다.
설계는 docs/genre-pipeline.md를 따른다.

- puzzle   : 미스터리·추리, 스릴러·호러
- world    : SF, 정통판타지
- style    : 실험장르 (일반문학 엔진 + 웹소설 엔진, 실행할 때 고른다)
- relation : 로맨스

하위 장르를 아직 고르지 않은 작품(``genre_lit``)과 다른 클러스터는 ``None``이다.
``None``이면 지금처럼 웹소설 러너를 탄다.
"""

from __future__ import annotations

import literary_form

GENRE_LITERATURE_CLUSTER = literary_form.GENRE_LITERATURE_CLUSTER

GROUP_PUZZLE = "puzzle"
GROUP_WORLD = "world"
GROUP_STYLE = "style"
GROUP_RELATION = "relation"
GROUPS = (GROUP_PUZZLE, GROUP_WORLD, GROUP_STYLE, GROUP_RELATION)

# genre_clusters.CLUSTER_SUBGENRE_MAP["genre_literature"]의 main_genre 값 (+ 예전 별칭)
GROUP_BY_MAIN = {
    "mystery": GROUP_PUZZLE,
    "mystery_detective": GROUP_PUZZLE,
    "detective": GROUP_PUZZLE,
    "thriller": GROUP_PUZZLE,
    "sf": GROUP_WORLD,
    "traditional": GROUP_WORLD,
    "experimental": GROUP_STYLE,
    "romance": GROUP_RELATION,
}

# 실행할 때 고르는 읽기 방식(엔진). 실험장르만 고를 수 있다.
ENGINE_LITERARY = "literary"
ENGINE_WEB = "web"
ENGINES = (ENGINE_LITERARY, ENGINE_WEB)


def _text(value: object) -> str:
    return str(value or "").strip()


def genre_group(project: object) -> str | None:
    """묶음 id(puzzle / world / style / relation) 또는 None."""
    if _text(literary_form._lookup(project, "cluster_id")) != GENRE_LITERATURE_CLUSTER:
        return None
    return GROUP_BY_MAIN.get(_text(literary_form._lookup(project, "main_genre")))


def engine_choice_available(project: object) -> bool:
    """실행할 때 문학/웹소설 줄을 고를 수 있는 작품인가 (실험장르)."""
    return genre_group(project) == GROUP_STYLE


def normalize_engine(value: object) -> str | None:
    key = _text(value).lower()
    if key in {ENGINE_LITERARY, "문학"}:
        return ENGINE_LITERARY
    if key in {ENGINE_WEB, "웹소설"}:
        return ENGINE_WEB
    return None


def resolve_engine(project: object, requested: object = None) -> str:
    """이 실행에서 쓸 엔진.

    - 실험장르가 아니면 요청 값을 무시한다. 일반문학은 문학 줄, 그 밖은 웹소설 줄이다.
    - 실험장르는 요청 값을 따르고, 없으면 문학 줄이 기본이다.
    """
    if literary_form.is_literary_track_target(
        literary_form._lookup(project, "cluster_id"),
        literary_form._lookup(project, "sub_genre"),
        literary_form._lookup(project, "main_genre"),
    ) and not engine_choice_available(project):
        return ENGINE_LITERARY
    if not engine_choice_available(project):
        return ENGINE_WEB
    return normalize_engine(requested) or ENGINE_LITERARY

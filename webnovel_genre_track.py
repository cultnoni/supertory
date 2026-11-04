"""웹소설 첨삭 파이프라인의 묶음 판별.

웹소설(``cluster_id=webnovel``) 하위 장르를 기존 AI 패널 기준집
(``data/genre_playbooks.json``) 구조에 맞춰 세 묶음으로 나눈다.
설계는 docs/webnovel-genre-pipeline.md를 따른다.

- web_romance        : 로맨스, 로판, BL, GL
- web_fantasy_male   : 판타지, 무협, 사극·대체역사, 스포츠, 남성향 판타지
- web_fantasy_female : 여성향 판타지(차원이동·현대·시대물)

하위 장르를 아직 고르지 않은 작품과 다른 클러스터는 ``None``이다.
``None``이면 지금처럼 묶음 없는 웹소설 러너를 탄다.

장르문학 전용 ``genre_track.py``와는 완전히 분리된 모듈이다(결정 사항 1번,
docs/webnovel-genre-pipeline.md). 장르문학 쪽 동작을 건드리지 않는다.
"""

from __future__ import annotations

import literary_form

WEBNOVEL_CLUSTER = "webnovel"

GROUP_ROMANCE = "web_romance"
GROUP_FANTASY_MALE = "web_fantasy_male"
GROUP_FANTASY_FEMALE = "web_fantasy_female"
GROUPS = (GROUP_ROMANCE, GROUP_FANTASY_MALE, GROUP_FANTASY_FEMALE)

# main_genre만으로 갈라지는 값. romance/romfant는 sub_genre(bl/gl/period_* 등)가 달라도 로맨스형.
# 무협·현판·정통판타지·이세계·대체역사·스포츠는 main_genre=fantasy, sub_genre=male,
# genre_detail=murim/urban/traditional/isekai/alt_history/sports로 저장된다
# (genre_clusters.GENRE_DETAIL_OPTIONS[("fantasy","male")] 참고, sub_genre로 아래에서 가른다).
# martial/historical/sports는 이 정규화 이전의 예전 저장값이라 호환을 위해 남겨 둔다
# (genre_clusters.CLUSTER_SUBGENRE_MAP["webnovel"]의 레거시 항목과 같은 값).
GROUP_BY_MAIN = {
    "romance": GROUP_ROMANCE,
    "romfant": GROUP_ROMANCE,
    "female_fantasy": GROUP_FANTASY_FEMALE,
    "martial": GROUP_FANTASY_MALE,
    "historical": GROUP_FANTASY_MALE,
    "sports": GROUP_FANTASY_MALE,
}

# main_genre="fantasy"일 때는 sub_genre(male/female)로 가른다.
GROUP_BY_FANTASY_SUB = {
    "male": GROUP_FANTASY_MALE,
    "female": GROUP_FANTASY_FEMALE,
}

# 설정집 「장르 클리셰」 칸. 작가만 쓴다. 장르문학 「장르 약속」과는 저장 패턴을
# 공유하지 않는다(결정 사항 3번, docs/webnovel-genre-pipeline.md).
CLICHE_USED = "cliche_used"    # 의도적으로 쓴 클리셰: 카드로 만들지 않는다
CLICHE_AVOID = "cliche_avoid"  # 피하고 싶은 클리셰: 판정 기준으로 올린다
CLICHE_FIELDS = (CLICHE_USED, CLICHE_AVOID)
CLICHE_FIELD_MAX_CHARS = 8000


def _text(value: object) -> str:
    return str(value or "").strip()


def genre_group(project: object) -> str | None:
    """묶음 id(web_romance / web_fantasy_male / web_fantasy_female) 또는 None."""
    if _text(literary_form._lookup(project, "cluster_id")) != WEBNOVEL_CLUSTER:
        return None
    main = _text(literary_form._lookup(project, "main_genre"))
    if main == "fantasy":
        sub = _text(literary_form._lookup(project, "sub_genre"))
        return GROUP_BY_FANTASY_SUB.get(sub)
    return GROUP_BY_MAIN.get(main)


def cliche_values(project: object) -> dict:
    """저장된 「장르 클리셰」 칸 값(없으면 빈 문자열)."""
    return {
        key: _text(literary_form._lookup(project, key))[:CLICHE_FIELD_MAX_CHARS]
        for key in CLICHE_FIELDS
    }

# 슈퍼토리 웹소설 장르 피드백 파이프라인 설계

Oct 29, 2026 · 초안

## 개요

웹소설(`cluster_id=webnovel`) 작품의 첨삭 피드백을 하위 장르별로 다르게 만든다. 방식은 장르문학 파이프라인(`docs/genre-pipeline.md`)과 같다. 웹소설 러너(`feedback_pipeline/runner.py`, 회차 단위)와 저장 구조, 히스토리, 수집, 중요도별 표시는 그대로 쓰고, 장르별 설정층(판단 기준, 프롬프트, 카드 유형, 검증 규칙)만 갈아끼운다.

웹소설의 목표는 "더 잘 읽히게"다. 장르문학·일반문학과 달리 의미·구조보다 **독자가 다음 화를 누르게 만드는 쾌감**이 먼저다. 그래서 판단 기준도 사이다감, 케미, 타격감, 전개 속도처럼 흥행 중심으로 잡는다. 가장 큰 위험도 다르다. 장르문학·일반문학은 "작가의 의도를 오류로 보는 것"이 위험이었는데, 웹소설은 그 위험에 더해 **장르 클리셰(회귀·환생·재벌남·계약연애 등)를 무조건 결함으로 지적하는 것**이 추가된다. 클리셰는 웹소설 장르의 약속이지 오류가 아니다. 의도적으로 쓴 클리셰는 존중하고, 신선함이 필요한 경우만 지적형으로 짚는다.

## 현재 상태 (2026-10-29 구현 완료)

0~6단계까지 반영된 상태다. 구현 전 코드 확인은 이 절 아래 참고용으로만 남긴다.

- 웹소설(`cluster_id=webnovel`)은 `webnovel_genre_track.genre_group()`으로 세 묶음(`web_romance` / `web_fantasy_male` / `web_fantasy_female`)을 가른 뒤 `genre_layers.LAYERS` SPEC을 얹어 돈다. 하위 장르가 비거나 매핑에 없으면 예전처럼 묶음 없이 웹소설 러너만 탄다.
- 설정집 「장르 클리셰」 칸(`cliche_used` / `cliche_avoid`)과 의도적 클리셰 제안(알림 → `cliche_used` 초안)이 연결되어 있다. 장르문학 「장르 약속」 칸은 건드리지 않았다.
- 첨삭이 넣는 기준집 범위는 도우미 A그룹과 같다(`genre_common.playbook_emphasis_text()`). 420자 자르기는 제거했다.
- `genre_track.py`(장르문학 전용)는 그대로 두었다. `genre_clusters.CLUSTER_HIDDEN_FEATURES["webnovel"]`는 빈 집합이다.

구현 전(2026-10-29 코드 확인)에는 첨삭이 `literary_track()`·장르문학 네 묶음으로만 갈라 웹소설은 러너 공통 경로만 탔고, `"장르: 로맨스 / 모던"` 한 줄과 공통 카드 8종뿐이었다. 기준집(`data/genre_playbooks.json` 6종 + delta 15종)은 AI 패널과 장르문학 관계형만 읽고 웹소설 첨삭에는 연결되지 않았다. `playbook_lookup_keys()` 매핑은 그때도 이미 있었다.

## 적용 대상과 분기

웹소설의 하위 장르 10개를 기존 기준집 구조를 따라 세 묶음으로 나눈다. 장르문학처럼 장르마다 전부 따로 설계하지 않는다. 묶음 경계를 기존 `genre_playbooks.json` 최상위 키(`romance_*` / `fantasy_male` / `fantasy_female`)와 똑같이 맞춰서, 쓰기로 되어 있던 기준집을 그대로 들여온다.

| 묶음 | id | 하위 장르 (`main_genre`/`sub_genre`) | 재사용하는 기존 기준집 | 판단 기준의 중심 |
|---|---|---|---|---|
| 로맨스형 | `web_romance` | 로맨스(`romance`), 로판(`romfant`), BL(`bl`), GL(`gl`) | `romance_modern` / `romance_romfant` / `romance_bl` / `romance_gl` + 해당 delta | 케미·밀당의 리듬, 갈등의 설득력, 관계 전개 속도 |
| 무협판타지형 | `web_fantasy_male` | 판타지(`fantasy`), 무협(`martial`), 사극·대체역사(`historical`), 스포츠(`sports`), 남성향 판타지(`male_fantasy`) | `fantasy_male` + 해당 delta(무협/정통무협/대체역사/현판/어반판타지/정통판타지/스포츠) | 성장 템포와 사이다감, 능력·설정 전달, 전투·대결의 타격감 |
| 여성향판타지형 | `web_fantasy_female` | 여성향 판타지(`female_fantasy`) | `fantasy_female` + 해당 delta(차원이동/현대/시대물) | 복수·성장·관계 서사의 균형, 반전·사이다 타이밍, 신분/세계 설정 |

- 판별은 `webnovel_genre_track.genre_group(project)`로 한다(구현 완료, 1단계). 반환값은 묶음 id 또는 `None`. `cluster_id=webnovel`이 아니면 `None`이다.
- **실제 저장값 교정(구현하며 확인한 내용, 설계 초안과 다름)**: 무협·현판·정통판타지·이세계·대체역사·스포츠는 `main_genre="fantasy"`, `sub_genre="male"`, `genre_detail=`무협/정통무협/현판/어반판타지/정통판타지/이세계/대체역사/스포츠로 저장된다(`genre_clusters.GENRE_DETAIL_OPTIONS[("fantasy","male")]`). `main_genre="martial"/"historical"/"sports"` 같은 단일 값은 이 정규화 이전의 **레거시 데이터**에서만 나온다. `webnovel_genre_track.genre_group()`은 `main_genre=="fantasy"`일 때 `sub_genre`(male/female)로 가르고, martial/historical/sports는 호환용으로 `GROUP_BY_MAIN`에 남겨 둔다. 여성향 판타지는 `main_genre="female_fantasy"`로 그대로 저장된다(현행 데이터, 레거시 아님).
- 묶음 안의 하위 장르 차이는 판단 기준 표의 "장르별 추가" 줄과, 이미 있는 delta 기준집으로 흡수한다. 새 delta를 만들 필요가 없다. 필요 항목 분기는 `genre_detail` 값으로 한다(`feedback_pipeline/genre_web_fantasy_male.py`의 `_EXTRA_BY_DETAIL`).
- 하위 장르가 비어 있거나(`main_genre` 미지정) 매핑에 없는 기존 작품은 `None`이라 지금처럼 묶음 없이 웹소설 러너를 탄다. 장르문학과 동일한 하위 호환 원칙.
- 같은 작품에서 BL/GL처럼 `sub_genre=blgl`인 경우 `genre_clusters.playbook_lookup_keys()`가 이미 `romance_bl`/`romance_gl`로 가려 준다. 이 로직을 그대로 쓴다.

### 재사용하는 엔진

장르문학과 달리 "두 줄 선택"이 필요 없다. 웹소설은 항상 웹소설 러너(`runner.run_feedback`, 회차 단위) 하나다. `feedback_pipeline/genre_common.py`·`genre_layers.py`의 범용 엔진(SPEC 기반 리포트·카드 생성)을 그대로 재사용하고, `LAYERS` 딕셔너리에 세 묶음을 추가로 등록한다. 장르문학 네 묶음과 웹소설 세 묶음이 같은 `genre_layers.LAYERS`에 공존하되, 그룹 id가 겹치지 않게 한다(`web_romance` ≠ `relation` 등 — 판단 기준이 완전히 다르므로 분리해야 한다. 로맨스라는 이름은 같지만 장르문학 로맨스는 "관계가 문학적으로 작동하는가", 웹소설 로맨스는 "다음 화가 궁금한가"를 본다).

화면·로그에서도 둘을 구분한다(결정 사항 2번). 각 묶음 모듈의 `MD_TITLE`(리포트 제목)과 `feedback_run.pipeline` 값을 "웹소설 로맨스 진단" / "장르문학 관계 진단"처럼 다르게 쓴다. 판단 기준이 완전히 다른 두 기능이 같은 이름으로 보이면 작가가 어떤 기준으로 평가됐는지 헷갈릴 수 있다.

## 장르 레이어: 설정층 4가지

장르문학과 동일한 네 가지를 웹소설 러너 단계마다 끼운다.

| 설정층 | 하는 일 | 놓이는 곳 |
|---|---|---|
| 판단 기준(루브릭) | 리포트가 보는 항목과 3단계 판정 기준 | 리포트 프롬프트 |
| 카드 유형 | 장르 전용 유형 태그, 수정안/지적형 구분, 중요도 | 카드 프롬프트, `type_guidance` |
| 검증 규칙 | 저장 전에 거르는 규칙 | `checks.py` 확장 |
| 작가 선언 | 의도적으로 쓴 클리셰, 피하고 싶은 클리셰 | 설정집 칸 |

프롬프트는 `feedback_pipeline/prompts/genre/<묶음 id>/`에 두어 장르문학 프롬프트(`puzzle`/`world`/`relation`)와 같은 자리에 나란히 둔다. 기존 `romance_modern` 등 AI 패널 기준집 텍스트는 프롬프트 조립 시 그대로 삽입하고(장르문학 관계형이 이미 쓰는 방식), 첨삭 전용 판단 기준은 별도로 추가한다. 모델은 `claude-sonnet-5`, 비용 상한은 실행당 2달러로 장르문학과 같다.

판정은 점수 없이 "잘 작동함 / 보완 여지 / 문제" 3단계, 모든 판정에 근거 위치(회차 번호 + 짧은 인용)를 붙인다.

## 묶음별 설계

### 로맨스형 (로맨스·로판·BL·GL)

| 항목 | 보는 것 |
|---|---|
| 케미 | 두 사람의 긴장·끌림이 설명이 아니라 장면으로 쌓이는가 |
| 밀당 호흡 | 밀고 당기는 리듬이 늘어지거나 너무 급하지 않은가 |
| 갈등의 설득력 | 관계를 가로막는 장애가 작위적이지 않은가 |
| 서브 비중 | 서브남/서브녀·라이벌이 메인 커플의 긴장을 가리지 않는가 |
| 클리셰 활용 | 장르 클리셰(재벌남, 계약연애, 신분차 등)를 의도적으로 쓰는가, 신선함이 필요한가 |

장르별 추가: 로판은 신분·사극 설정과 로맨스 결합의 개연성(`romance_modern__historical` 기준), BL/GL은 관계 역학의 일관성(`romance_bl`/`romance_gl` 기준).

카드 유형: 케미 약함(지적형), 밀당 리듬 어긋남(지적형), 갈등 작위적(지적형), 서브캐릭터 과잉(지적형), 클리셰 피로(지적형), 설정 모순(수정안, 기존 정합성 카드 재사용), 호칭·말투 불일치(수정안, 기존 정합성 카드 재사용).

### 무협판타지형 (판타지·무협·사극/대체역사·스포츠·남성향)

| 항목 | 보는 것 |
|---|---|
| 성장 템포 | 성장·보상이 독자가 기대하는 타이밍에 오는가 (너무 늦게/급하게 오지 않는가) |
| 설정 전달 | 능력치·무공·레벨 체계가 설정 덤프 없이 자연스럽게 전달되는가 |
| 타격감 | 전투·대결 장면의 긴장과 결과가 설명이 아니라 장면으로 전달되는가 |
| 장르 공식 활용 | 회귀·환생·이세계 전개 공식을 의도적으로 쓰는가, 반전이 필요한가 |

장르별 추가: 무협은 문파·배분(세대) 설정의 일관성(`fantasy_male__murim`/`__murim_classic`), 정통판타지는 세계 규칙의 일관성(`__traditional`), 대체역사는 역사적 사실과의 교차점 처리(`__alt_history`), 현판/어반판타지는 현실 설정과 능력의 결합(`__urban`/`__hidden_world`), 스포츠물은 경기·훈련 장면의 리듬(`__sports`).

카드 유형: 성장 템포 어긋남(지적형), 설정 덤프(지적형), 타격감 부족(지적형), 능력치·설정 불일치(수정안, 설정집 대조), 용어 불일치(수정안, 토리 사전 대조).

### 여성향판타지형 (여성향 판타지: 차원이동·현대·시대물)

| 항목 | 보는 것 |
|---|---|
| 서사 균형 | 복수·성장·관계 서사가 서로를 방해하지 않고 같이 쌓이는가 |
| 반전·사이다 타이밍 | 역전·복수의 타이밍이 쌓인 서러움/갈등에 맞게 오는가 |
| 감정선 | 감정이 설명에 그치지 않고 장면·행동으로 드러나는가 |
| 세계·신분 설정 | 환생·이세계·신분 설정이 일관되게 쓰이는가 |

장르별 추가: 시대물은 사극 설정과 신분 질서(`fantasy_female__period_east`/`__period_west`), 차원이동물은 원래 세계와 이동한 세계의 규칙 구분(`__dimension`).

카드 유형: 서사 균형 어긋남(지적형), 감정 설명 과잉(지적형), 반전 타이밍 어긋남(지적형), 신분·세계 설정 모순(수정안, 설정집 대조).

## 설정집과 작가 선언

웹소설은 이미 흥행작 프로파일 연결, 떡밥 모음 등을 쓰고 있어 설정집 구조를 새로 만들 필요는 없다. 다만 "의도한 클리셰를 결함으로 보지 않기" 위한 선언 칸이 없다. 장르문학의 「장르 약속」 칸과 같은 역할을 웹소설에도 추가한다.

- 설정집에 「장르 클리셰」 항목을 신설하고 칸 2개를 둔다. 1) 의도적으로 쓴 클리셰(예: "계약 연애, 재벌남 설정은 의도"). 2) 피하고 싶은 클리셰(예: "나쁜 남자 서사는 지양"). 작가만 쓴다.
- 저장 패턴은 장르문학 「장르 약속」 칸(`genre_track.PROMISE_BROKEN`/`PROMISE_KEPT`)을 재사용하지 않고 **완전히 새로 설계한다**(결정 사항 3번). 웹소설 클리셰는 장르문학의 "일부러 깬 약속"보다 훨씬 가벼운 개념이라, 칸 이름·안내 문구·판정 반영 방식을 웹소설 어휘에 맞춰 따로 잡는다. 구체 설계(컬럼명, 글자 수 제한, 프롬프트 반영 위치)는 1단계 구현 시 정한다.
- 1번에 적힌 클리셰는 카드로 만들지 않는다. 리포트에서는 "의도는 알지만 이 장면에서는 신선함이 부족합니다" 정도로만 말할 수 있다.
- 2번은 판정 기준으로 올려 중요도를 높인다.
- 같은 유형을 반복해 거절하면 의도적 선택을 제안하는 기능(장르문학 7단계, `genre_intent_suggest.py`)을 웹소설 묶음에도 그대로 연결한다. 유형 태그만 다를 뿐 로직은 같다.

## 검증

저장 전 검증은 웹소설 기존 검사(이름·용어 필터, "AI 검사, 확인 필요" 라벨)를 모두 유지한다. 장르 레이어는 아래를 더한다.

1. 장르 판정의 근거 인용이 실제 원고에 있는가 (없으면 제거)
2. 장르 클리셰를 확정 결함으로 쓰지 않았는가, "의도라면 유지"가 붙어야 하는 곳에 붙었는가
3. 의도적으로 쓴 클리셰(1번 칸)에 해당하는 카드가 섞이지 않았는가
4. 수정안이 케미·타격감·반전 타이밍에 영향을 주는 핵심 장면을 건드리지 않았는가 (그렇다면 지적형으로 강등)
5. 이전 회차 언급에 회차 번호가 있는가, 설정집에서 확인되지 않으면 단정 대신 질문으로 바꾼다

걸러진 카드는 버리지 않고 지적형으로 바꿔 살리는 것이 기본값이다(장르문학과 동일).

## 구현 구조

- `genre_track.py`(장르문학 전용)는 건드리지 않는다. 같은 패턴의 **별도 모듈 `webnovel_genre_track.py`**를 새로 둔다(결정 사항 1번). `genre_group(project)`와 모양이 같은 `webnovel_genre_group(project)` 함수를 두되, 완전히 독립된 모듈이라 장르문학 쪽 회귀 위험이 없다. `cluster_id` 분기 로직이 두 모듈에 비슷하게 중복되는 건 감수한다.
- `feedback_pipeline/genre_layers.py`: `LAYERS` 딕셔너리에 `web_romance`/`web_fantasy_male`/`web_fantasy_female` 세 모듈을 추가로 등록한다. `genre_common.run_report()`는 고치지 않는다.
- `feedback_pipeline/genre_web_romance.py`, `genre_web_fantasy_male.py`, `genre_web_fantasy_female.py`(새 모듈 3개): 각각 `SPEC`, `build_cards`, `protected_paragraphs`, `MD_TITLE`, `MD_LABELS`를 가진다. 장르문학 레이어 모듈(`genre_puzzle.py` 등)과 같은 모양.
- 기존 기준집 삽입: `app.load_genre_playbook()` / `load_genre_playbook_delta()`를 그대로 불러와 프롬프트에 삽입한다(`genre_relation.py:62-94`의 `_playbook_text()`와 같은 방식). 첨삭 전용 판단 기준은 이 텍스트 뒤에 덧붙인다.
- `feedback_api.py`: `genre_track` 분기 옆(또는 같은 분기 안)에 웹소설 묶음 판별을 추가한다. 일반문학·장르문학처럼 묶음이 있으면 레이어를 얹어 실행하고, 없으면 지금처럼 순수 웹소설 러너만 돈다.
- `feedback_pipeline/prompts/genre/web_romance/`, `web_fantasy_male/`, `web_fantasy_female/`: 묶음별 리포트·카드 프롬프트와 스키마.
- DB: 카드 유형 태그·`feedback_run.pipeline`은 CHECK 제약이 없어 마이그레이션 없이 새 값(`genre_web_romance` 등)을 바로 쓸 수 있다(결정 사항 4번).
- 설정집: 「장르 클리셰」 칸 2개 추가 — 새 컬럼 마이그레이션(장르문학과 다른 저장 패턴, 결정 사항 3번) + `ko/en/es` 로케일 문구.

## 구현 순서

- [x] 0단계 설계 확정: 결정 사항 1~4번 (「결정 사항」 참고)
- [x] 1단계 기반: 웹소설 묶음 판별(`webnovel_genre_track.py`), 설정집 「장르 클리셰」 칸 2개(`db/106_webnovel_cliche.sql`, `routes/webnovel_cliche_api.py`) + 화면·로케일. `genre_layers.LAYERS` 등록은 실제 묶음 모듈(SPEC)이 생기는 2~4단계에서 같이 한다.
- [x] 2단계 로맨스형 리포트·카드·검증 (`feedback_pipeline/genre_web_romance.py`, 프롬프트는 `feedback_pipeline/prompts/genre/web_romance_*`). 기존 `genre_common.py`를 `broken_field`/`kept_field`(기본값 `promise_broken`/`promise_kept`)로 일반화해 `cliche_used`/`cliche_avoid`를 그대로 꽂았다. `tests/test_genre_web_romance.py` 15개 포함 회귀 테스트(`test_genre_*` 261개, schema/web_js_undef) 전부 통과.
- [x] 3단계 무협판타지형 리포트·카드·검증 (`feedback_pipeline/genre_web_fantasy_male.py`, 프롬프트는 `feedback_pipeline/prompts/genre/web_fantasy_male_*`). `genre_detail`로 무협/정통무협/정통판타지/대체역사/현판/어반판타지/스포츠 추가 항목을 가른다(`_EXTRA_BY_DETAIL`). `main_genre="historical"/"sports"` 레거시 값은 `_LEGACY_MAIN_TO_DETAIL`로 같은 효과를 낸다. `tests/test_genre_web_fantasy_male.py` 14개 포함 회귀 테스트 전부 통과.
- [x] 4단계 여성향판타지형 리포트·카드·검증 (`feedback_pipeline/genre_web_fantasy_female.py`, 프롬프트는 `feedback_pipeline/prompts/genre/web_fantasy_female_*`). `sub_genre`로 차원이동(`world_split`)·동양/서양 시대물(`status_order`) 추가 항목을 가른다. 현대(`modern`)는 추가 루브릭 키 없이 기준집 delta만 넣는다. `main_genre="female_fantasy"`만 이 묶음이고, `genre_detail`은 비어 있다. `tests/test_genre_web_fantasy_female.py` 17개 통과.
- [x] 5단계 의도적 클리셰 제안 연결 (`genre_intent_suggest.py` 재사용). 카드 집계(`GENRE:` + `style_type`)는 클러스터와 무관해 그대로 썼다. `cluster_id=webnovel`이면 알림이 「장르 클리셰」(`cliche_used`)를 열고, 장르문학은 기존대로 「장르 약속」(`promise_broken`)을 연다. 초안 프롬프트는 `intent_cliche_draft_prompt.txt`로 분리했다. `tests/test_genre_web_intent_suggest.py` + 기존 `test_genre_intent_suggest.py` 통과.
- [x] 6단계 AI 패널 프롬프트와의 중복 점검. 첨삭 `_playbook_text()`가 420자로 자르고 세부장르 `A_judge`·로맨스 참고 어휘를 빠뜨려, 도우미는 「회귀는 장르 장치」인데 첨삭은 그 문장을 못 보는 상태였다. `genre_common.playbook_emphasis_text()`로 도우미 A그룹(점검·판정 지침·세부장르 추가 기준·참고 어휘)과 같은 범위를 자르지 않고 넣도록 맞췄다. `tests/test_genre_web_playbook_overlap.py`가 로맨스·무협·여성향 대표 문장을 도우미 설정 검사 프롬프트와 첨삭 기준집에서 같이 확인한다.

로맨스형을 먼저 하는 이유는 기존 기준집(`romance_modern`/`romfant`/`bl`/`gl`)이 가장 완성도가 높고, 장르문학 관계형 묶음에서 이미 한 번 재사용해 본 경로라 리스크가 작기 때문이다.

## 구현 메모

0~6단계는 끝냈다.

1~3단계에서 자리잡은 패턴이다. 4단계(여성향판타지형)도 그대로 따랐다.

- **새 묶음 모듈을 만들 때 복사할 템플릿**: `feedback_pipeline/genre_web_fantasy_male.py`(무협판타지형)가 가장 최근이자 가장 복잡한 예다. 여성향판타지형은 `main_genre="female_fantasy"` 하나만 보면 되므로 오히려 `genre_web_romance.py`(로맨스형)에 더 가깝다 — `sub_genre`(dimension/modern/period_east/period_west)로 "장르별 추가" 항목을 가르는 구조가 로맨스형의 genre_detail 분기와 비슷하다.
- **체크리스트** (파일 4종 + 테스트):
  1. `feedback_pipeline/prompts/genre/web_fantasy_female_system.txt` / `_map_prompt.txt` / `_map_schema.json` / `_rubric_prompt.txt` / `_rubric_schema.json` 5개 작성 (web_fantasy_male 파일을 복사해서 문구만 바꾸는 게 제일 빠르다).
  2. `feedback_pipeline/genre_web_fantasy_female.py` 작성: `GROUP = webnovel_genre_track.GROUP_FANTASY_FEMALE`, `SPEC`에 `broken_field=webnovel_genre_track.CLICHE_USED`, `kept_field=webnovel_genre_track.CLICHE_AVOID` 반드시 넣기(이미 `genre_common.py`가 일반화되어 있어 이것만 넣으면 됨). `_playbook_text()`는 `app.load_genre_playbook("female_fantasy", sub_genre)` 호출(= romance/fantasy_male과 같은 방식, `genre_clusters.playbook_lookup_keys`가 내부에서 `("fantasy","female",sub)`로 바꿔 준다).
  3. `feedback_pipeline/genre_layers.py`의 import와 `LAYERS` 딕셔너리에 한 줄씩 추가.
  4. `tests/test_genre_web_fantasy_female.py`를 `test_genre_web_fantasy_male.py`를 복사해서 작성. `_genre_harness.create_scene()`는 이미 `purpose`/`cluster_id`를 받게 일반화되어 있으니 `create_scene("female_fantasy", purpose="web_novel", cluster_id="webnovel", sub_genre="dimension")` 형태로 바로 쓸 수 있다.
  5. `docs/webnovel-genre-pipeline.md`의 4단계 체크박스를 `[x]`로 바꾸고 구현 메모를 남긴다. (완료)
- **놓치기 쉬운 점**:
  - `main_genre="fantasy"`일 때만 `sub_genre`로 갈라야 한다(무협판타지형 vs 여성향판타지형이 둘 다 `fantasy` main을 쓸 수 있다는 뜻은 아니다 — 여성향은 `main_genre="female_fantasy"`로 완전히 다른 값이다. `webnovel_genre_track.genre_group()`을 다시 보면 헷갈리지 않는다).
  - `_genre_harness.create_scene()`에 `purpose`/`cluster_id`를 안 넘기면 기본값이 `genre_literature`로 떨어진다. 웹소설 테스트는 항상 `purpose="web_novel", cluster_id="webnovel"`을 명시한다.
  - 새 프로젝트 생성 시 `main_genre="fantasy"`인데 `sub_genre`를 안 넘기면(테스트에서 깜빡하기 쉬움) `genre_group()`이 `None`을 돌려줘서 장르 레이어가 아예 안 탄다 — 3단계에서 실제로 겪은 실수다.
- **4단계 결과**: 지도 키는 `narrative_map`(서사 지도), 비용 단계 이름은 `genre_narrative_map`. 기본 항목은 `narrative_balance` / `payoff_timing` / `emotion_line` / `world_status`. 추가 항목은 `sub_genre=dimension` → `world_split`, `period_east`/`period_west` → `status_order`. `modern`은 추가 키가 없다. 카드는 전부 지적형이고, 신분·세계 설정 모순의 수정안은 기존 정합성 카드에 맡긴다. 화면 제목은 「웹소설 여성향판타지 진단」.
- **5단계 결과**: 집계 쿼리는 `genre_track`에 묶여 있지 않았다(`GENRE:` + `style_type`만 본다). 묶여 있던 것은 알림이 여는 칸(`promise_broken`)뿐이라, `genre_common`처럼 `spec_for_cluster()`로 칸·문구만 갈랐다. 별도 모듈을 두지 않은 이유: 집계·임계값·스누즈는 같고, 장르문학 기본값(`section=promise`)을 그대로 두면 회귀 위험이 없다. 알림 「추가」는 `cliche_used`에 초안을 덧붙인다(`appendWebnovelClicheDraft`). `cliche_avoid`는 작가가 직접 쓰는 칸이라 거절 제안 대상이 아니다.
- **5단계(의도적 클리셰 제안)**: 장르문학의 `feedback_pipeline/genre_intent_suggest.py`를 열어서, `report_ref`가 `GENRE:`로 시작하는 카드를 유형별로 집계하는 로직이 `genre_track`(장르문학 전용)에 묶여 있는지 먼저 확인할 것. 묶여 있다면 `webnovel_genre_track`도 함께 보도록 일반화하거나(이번 `genre_common.py`처럼), 웹소설 전용으로 따로 둘지 결정 사항 1번과 같은 기준(장르문학 회귀 위험)으로 다시 판단한다. 알림이 여는 설정집 칸은 「장르 약속」이 아니라 「장르 클리셰」(`cliche_used`/`cliche_avoid`)여야 한다. (완료)
- **6단계 결과**: 어휘가 어긋날 자리는 모델 출력이 아니라 프롬프트에 넣는 기준집 범위였다. 첨삭은 필드당 420자로 잘라 「판타지 장치 오판 방지」 같은 핵심 문장이 잘렸고, 세부장르 `group_rules_addition.A_judge`(공수 전환·고증 금지·이중잣대 등)와 로맨스 `[캐릭터/관계 참고 어휘]`가 빠져 있었다. 톤·점검·피할 것·판정 지침은 유지하되 자르지 않고, 세부장르 판정 지침·피할 것·참고 어휘를 도우미 A그룹과 같이 넣었다. 모델 실출력 비교는 Gemini 설정 검사 기존 live 테스트가 이미 있고(공수 전환·정통무협은 모델 흔들림으로 실패할 수 있음), 첨삭 실출력까지 맞추는 것은 비용·재현성 때문에 프롬프트 교집합 테스트로 고정했다. 도우미 쪽 대표 live 3건(`test_live_romance_does_not_flag_regression_as_genre_break`, `test_live_fantasy_female_does_not_flag_shy_heroine`, `test_live_romance_analyze_does_not_flag_obsession_or_timeslip_as_break`)은 2026-10-29에 다시 돌려 통과했다.
- **6단계(AI 패널 중복 점검)**: 코드 변경이 아니라 실제 실행 비교다. 같은 작품에 대해 AI 패널(도우미)의 장르 조언과 첨삭 리포트의 장르 진단이 서로 다른 말을 하지 않는지 수동으로 몇 건 돌려서 확인하고, 어긋나면 `_playbook_text()`가 뽑아 쓰는 범위(톤/점검/피할 것/판정 지침)를 조정한다. 열린 질문 1번이 바로 이것이다. (완료)

## 테스트 계획

장르문학과 같은 원칙이다. 오지적(클리셰를 결함으로 지적하는 것)을 먼저 잡고 탐지율은 그다음이다.

| 묶음 | 내용 | 측정 목적 |
|---|---|---|
| 흥행작 (동의받았거나 공개된 인기작 일부) | 오지적 |
| 결함 심은 회차 | 설정 모순, 타격감 부족, 케미 약함을 일부러 삽입 | 탐지율 |
| 클리셰를 의도적으로 쓴 회차 | 「장르 클리셰」 칸을 채운 버전과 비운 버전 | 의도 존중 |
| 실제 사용자 원고 | 동의받은 웹소설 작가의 회차 | 실사용 가치 |

| 항목 | 재는 법 | 초기 목표 |
|---|---|---|
| 근거 인용 존재 | 자동 확인 | 100% |
| 심은 결함 탐지율 | 결함을 맞는 항목으로 잡은 비율 | 70% 이상 |
| 클리셰 오지적 | 의도 칸을 채웠을 때 결함으로 지적한 횟수 | 회차당 1건 이하 |
| 재현성 | 같은 입력 3회 실행 시 판정 일치 | 뒤집힘 없음 |
| 비용·시간 | 회차 1개 기준 토큰, 소요 시간 | 기준값 기록 |

## 결정 사항

2026-10-29에 정했다.

1. **모듈 구조**: `genre_track.py`(장르문학 전용)는 건드리지 않는다. 별도 모듈 `webnovel_genre_track.py`를 새로 둔다. 기존 장르문학 7단계 구현이 깨질 위험을 없애는 쪽을 우선한다. `cluster_id` 분기 로직이 두 모듈에 비슷하게 중복되는 건 감수한다.
2. **이름 충돌**: 장르문학 관계형(`relation`)과 웹소설 로맨스형(`web_romance`)은 화면·로그에서 표시 이름을 구분한다. 각 묶음의 `MD_TITLE`과 `feedback_run.pipeline` 값을 다르게 쓴다("웹소설 로맨스 진단" / "장르문학 관계 진단" 등).
3. **저장 패턴**: 웹소설 「장르 클리셰」 칸은 장르문학 「장르 약속」 칸(`genre_track.PROMISE_BROKEN`/`PROMISE_KEPT`)의 저장 패턴을 재사용하지 않고 완전히 새로 설계한다. 웹소설 클리셰는 장르문학의 "일부러 깬 약속"보다 가벼운 개념이라 칸 이름·안내 문구를 웹소설 어휘에 맞춰 따로 잡는다. 컬럼명·글자 수 제한 등 구체 설계는 1단계 구현 시 정한다.
4. **카드 유형 태그 DB 제약 (해결)**: `feedback_card.style_type`, `feedback_run.pipeline` 모두 CHECK 제약이 없는 자유 TEXT 컬럼이다(`db/094_feedback_run.sql:50`, `db/099_literary_short_report.sql:4`). 새 카드 유형·파이프라인 이름 추가에 마이그레이션이 필요 없다.

## 열린 질문

1. AI 패널(도우미)이 이미 쓰는 기준집 텍스트를 첨삭 프롬프트에 그대로 삽입했을 때, 도우미 답변과 첨삭 카드의 어휘·판단이 어긋나지 않는지 — 6단계에서 프롬프트 교집합으로 닫았다. 모델 실출력은 같은 기준집을 읽어도 흔들릴 수 있다. 출시 전 사람 검수는 별도다.

## 영어·스페인어권 영향

화면 문구(카드 유형 이름, 설정집 「장르 클리셰」 칸, 안내)는 `ko/en/es` 로케일에 함께 추가한다. 첨삭 프롬프트와 결과는 한국어 원고를 전제로 하며, 영어·스페인어 작품 지원은 이 설계의 범위가 아니다(`AGENTS.md`의 언어 검토 목록 참고).

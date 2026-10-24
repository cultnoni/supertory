# AI 기능별 연결 목록

2026-10-24 코드 조사 기준. 호출 위치는 `generate_text(` / 첨삭 파이프라인 호출을 전부 찾아 만들었고, 기능 이름은 호출하는 함수·라우트 이름에서 읽었다. 모델을 바꾸거나 19금 전용 AI를 붙이기 전에 이 표를 먼저 갱신한다.

## Gemini

Gemini 호출은 모두 `gemini_client.generate_text` 하나를 거친다. 호출마다 모델을 고르는 인자가 없어서 전부 `GEMINI_MODEL` 한 개를 같이 쓴다. 기본값은 `gemini-flash-lite-latest`(`gemini_client.DEFAULT_MODEL`, `.env.example`).

| 기능 | 호출 위치 | 비고 |
|---|---|---|
| AI 패널 (이어쓰기, 다시쓰기, 요약, 분석, 브레인스토밍, 세계관 스캔, 복선, 반전, 중복체크, 묘사 확장, 유사어, 문체 스며듦, 부제 등 약 28개 모드) | `app.py` `ai_assist` | 한 함수가 모드별로 갈라진다. 호출 2곳, 한 곳은 온도 0.9 |
| 토리·인물 대화, 인물 토론 | `app.py` `ai_assist` | `chat`, `character_chat`, `chardebate` 모드 |
| 맞춤법 보조 검사 | `app.py` `_spellcheck_with_gemini` | 공개 검사기가 막히면 대신 쓴다 |
| 회차 요약 색인, 작품 색인 병합 | `app.py` `summarize_scene_for_index`, `merge_project_index` | 온도 0.2 |
| 회차 성향·아이템 분석 | `app.py` `generate_scene_trait_analysis`, `generate_scene_item_analysis` | |
| 인물 분석 작업 | `app.py` `_run_character_analysis_job` | 호출 2곳 |
| 인물 관계 제안, 회차 출연 후보 제안 | `app.py` `suggest_character_relations`, `suggest_scene_cast_candidates` | |
| 범위 밖 수치 보정 | `app.py` `_out_of_range` | |
| 가상독자 댓글 | `routes/reader_api.py` `generate_scene_reader_comments` | |
| 가상독자 대화 | `routes/reader_api.py` `reader_chat` | |
| 가상독자 토론 | `routes/reader_api.py` `reader_debate` | |
| 글럼프 14종 (하이라이트 추출, 와일드카드, 빈칸 채우기, 핑퐁, 행운 문장, 심문 시작·요약, 분위기 색·플레이리스트·무드보드, 단어·문장 목록, 인물 타로, 작명소) | `routes/glump_api.py` | 호출 14곳 |
| 흥행 공식 분석 | `routes/success_pattern_api.py` | 호출 2곳 |
| 번역 배치 | `services/translation_batch_service.py` | |
| 교정고 비교 | `proof_diff.py` | |
| 교정고 회차 매칭 | `chapter_match.py` | |
| 가져오기 목차 자동 추출 | `import_hierarchy.py` | |

## Claude (첨삭 피드백 파이프라인)

첨삭 파이프라인에만 쓴다. 모델은 `feedback_pipeline/config.py`에서 단계별로 정한다.

| 기능 | 모델 |
|---|---|
| 웹소설·문학 첨삭 6단계 (문체, 장면 지도, 기준, 보고서, 검증, 카드) | `claude-sonnet-5` |
| 장편 이전 단위 요약 압축 | `claude-haiku-4-5-20251001` |
| 카드 질문 답변 | `claude-sonnet-5` |
| 카드 수정안 요청 (`feedback_api.py`) | `claude-sonnet-5` |

- 실행당 비용 상한 2달러, 중간 점검 6달러(`MAX_COST_USD_PER_RUN`, `MAX_COST_USD_MIDCHECK`).
- Opus는 어디에도 쓰이지 않는다(`claude-opus-5`는 샘플링 잠금 목록에만 있다).

## 눈에 띄는 점

- 번역(품질이 중요)과 글럼프·색인(가벼운 작업)이 같은 flash-lite를 쓴다. 모델 점검의 첫 작업은 Gemini 호출을 용도별로 나누는 것이다.
- 19금 작품 전용 AI 연결도 같은 구조 변경(호출별 모델 인자)이 먼저 필요하다.
- 아직 안 한 것: `ai_assist`의 모드별로 어느 기능 설정(클러스터)에 묶이는지 상세 표, 모델 이름이 최신인지 확인.

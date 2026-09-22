# 첨삭 피드백 HTTP API

화면 도크는 분석 메뉴의 **첨삭 피드백 (새 방식)** 에서 엽니다. 클라이언트가 실행을 만든 뒤 `GET /api/feedback/runs/{run_id}`로 2초마다 폴링합니다. API 키 값은 어떤 응답에도 포함되지 않습니다.

모델: `claude-sonnet-5`. 서버가 시작될 때 `status=running`으로 남은 실행은 리포트가 있으면 `partial`, 없으면 `failed`로 바꾸고 `progress.stage`를 `interrupted`로 둡니다.

개발 실행(`python app.py`, 저장소의 `data/supertory.sqlite3`)과 Electron/설치본(`%APPDATA%\supertory\data\supertory.sqlite3`)은 DB 위치가 달라 피드백 기록이 공유되지 않습니다. 한쪽에서 돌린 첨삭은 다른 쪽에서 보이지 않습니다.

## 상태 값

실행 `status`: `running` → `ok` | `partial` | `failed`

카드 `status`: `open` | `applied` | `applied_edited` | `ignored` | `alternate`

카드 `priority`: `high` | `medium` | `low` | `ref`

`fixable=none` 약점은 수정안 없는 참고 카드(`kind=style`, `warnings_json`에 `note_only`)로 남습니다. 화면은 「참고 지적」 라벨만 붙이고 「AI 검사, 확인 필요」는 쓰지 않습니다.

`params.dropped`는 카드가 되지 못한 항목을 `{id, title, reason}`로 남깁니다. `reason` 코드: `p2_filtered`, `quote_invalid`, `range_invalid`, `cap_reached`, `no_original_range`, `generation_failed`.

## 폴링

1. `POST /api/projects/{project_id}/feedback/runs`로 실행을 만든다. 본문은 서버가 현재 revision HTML에서 문단으로 나눈다.
2. 1~2초 간격으로 `GET /api/feedback/runs/{run_id}`를 호출한다.
3. `status`가 `running`이 아니면 끝. `params.progress`(또는 `progress`)로 단계를 표시하고, `usage.cost_usd`로 추정 비용을 본다.
4. `paragraphs`는 `{i, type, text}` 배열이다. 화면 파서와 같은지 대조할 때 쓴다.

동시에 도는 실행은 최대 2개입니다. 같은 회차에 `running`이 있으면 409입니다.

## 엔드포인트

### `GET /api/feedback/status`

```json
{ "configured": true, "model": "claude-sonnet-5", "default_explanation_lens": "normal" }
```

`project_id` 쿼리를 넣으면 `default_explanation_lens`가 붙습니다(`feedback_pipeline.context.explanation_lens_for_project`). 웹소설 계열은 `normal`(보통), 문학·에세이 계열은 `strong`(자세히). 화면의 분석 강도 기본값으로 씁니다.

`SUPERTORY_FEEDBACK_FAKE=1`이면 `configured`는 키가 없어도 true이고 `"fake": true`가 붙습니다. 키 값은 절대 넣지 않습니다. 이 값은 **지금 서버가 시험 모드인지**만 알려 줍니다. 화면에서는 [새로 분석] 옆 「다음 분석은 시험 모드로 실행돼요」로만 씁니다.

각 실행의 시험/실제 여부는 `params.fake`(true/false)입니다. runner가 실행을 만들 때 기록합니다. 옛 기록에 키가 없으면 화면 배지·드롭다운에 시험/실제 문구를 붙이지 않습니다.

Windows에서 시험 모드로 실행하는 예:

```bat
set SUPERTORY_FEEDBACK_FAKE=1
set SUPERTORY_FEEDBACK_FAKE_DELAY=0.7
python app.py
```

PowerShell:

```powershell
$env:SUPERTORY_FEEDBACK_FAKE="1"
python app.py
```

### `POST /api/projects/{project_id}/feedback/runs`

요청:

```json
{ "scene_id": 12, "explanation_lens": "normal" }
```

`explanation_lens`는 `strong` | `normal` | `off` (생략 가능). 화면은 보통=`normal`, 자세히=`strong`을 넣고, 마지막 선택을 프로젝트별 localStorage에 기억합니다. 시험용으로 `max_cost`를 넣을 수 있습니다.

응답 `201`:

```json
{ "run_id": 1, "source_hash": "…", "paragraph_count": 20, "status": "running" }
```

오류: 키 없음 `400`, 회차 없음 `404`, 같은 회차 실행 중·슬롯 가득 `409`.

### `GET /api/projects/{project_id}/feedback/runs?scene_id=`

실행 목록(리포트 본문 없음, 카드 수 포함). `scene_id`를 빼면 프로젝트 전체. 각 행에 대표 회차 `scene_id`·`scene_title`이 붙습니다(없으면 null/빈 문자열).

### `GET /api/feedback/runs/{run_id}`

실행 한 건. `scenes[].paragraphs`, 편의 필드 `paragraphs`, `report` / `report_md`, `cards`(JSON 컬럼 파싱됨), `progress`, `usage`. `params.fake`는 그 실행이 시험 모드로 만들어졌는지를 나타냅니다.

검토 모드 레이아웃: 원고 열 높이는 검토를 열어도 줄이지 않습니다. 이후 하단 작업 바는 원고 열을 줄이지 않고, 바가 보일 때만 에디터 스크롤 영역 위 오버레이(하단에서 12px)로 그립니다. 그때만 스크롤 영역에 `padding-bottom`(바 높이+여백)을 줍니다.

### `PUT /api/feedback/cards/{card_id}`

```json
{ "status": "applied_edited", "final_text": "고친 문장" }
```

`final_text`는 `applied_edited`일 때만 받습니다. 잘못된 상태는 `400`.

### `GET /api/feedback/cards/{card_id}/comments`

그 카드의 의견 대화를 시간순으로 돌려줍니다. `{ "comments": [{ "id", "card_id", "run_id", "project_id", "role", "body", "created_at" }] }`. `role`은 `user` | `assistant`. 카드가 없으면 `404`.

### `POST /api/feedback/cards/{card_id}/comments`

```json
{ "message": "대화가 길어서 설명을 넣은 건데요." }
```

그 카드 하나에만 짧게 답합니다. 응답 `{ "comments": [사용자, 토리], "usage": { "input_tokens", "output_tokens", "cost_usd" } }`. 카드당 20턴을 넘으면 호출 없이 `409`. 호출이 실패하면 사용자 메시지는 남기고 `502`.

`GET /api/feedback/runs/{run_id}`의 각 카드에는 `comment_count`가 붙습니다. 본문은 위 GET으로 따로 가져옵니다.

### `POST /api/feedback/runs/{run_id}/primary`

```json
{ "scene_id": 12 }
```

### `POST /api/feedback/runs/{run_id}/cancel`

실행 중이면 취소 플래그를 세웁니다. 파이프라인이 단계·카드 사이에서 멈추고 `partial`이 되며, 그때까지 저장된 카드·리포트는 남습니다.

### `DELETE /api/feedback/runs/{run_id}`

실행 중이면 `409`. 그 외에는 실행·카드·스냅샷을 삭제합니다.

### `POST /api/projects/{project_id}/feedback/import-legacy`

```json
{ "entries": [{ "id": "local-1", "mode": "analyze", "text": "옛 리포트", "sceneId": 12 }] }
```

응답: `{ "imported": 1, "skipped": 0, "run_ids": [3] }`. 같은 `id`는 건너뜁니다.

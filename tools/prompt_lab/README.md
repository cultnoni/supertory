# 프롬프트 랩

앱 DB와 무관하게, 원고 파일과 `cases.json`으로 첨삭 카드 프롬프트를 시험합니다.  
`prompts/` 안의 템플릿은 코드와 분리되어 있으니 그 파일만 고치면 됩니다.

## 실행

저장소 루트에서:

```bash
python tools/prompt_lab/run.py --cases all --runs 1
```

- `--models` 생략 시 `gemini_client.DEFAULT_MODEL`과 `gemini-flash-latest` 두 개를 씁니다.
- `--cases C01,R06` 처럼 카드 케이스만 고르면 검증기·맞춤법은 돌리지 않습니다.
- `--verifier-only` 검증기 단독 시험만, `--spell-only` 맞춤법만.
- `--verifier-only --cases VB1,VB6` 이면 검증기 케이스만 고릅니다.
- `--verify-prompt v1|v2` 검증 프롬프트 버전. 기본 `v1`(`prompts/verify_prompt.txt`). `v2`는 `prompts/verify_prompt_v2.txt`이며, 응답의 `source_units` / `new_units` / `reversals` / `repeats`에서 네 판정 플래그를 계산합니다.
- `--card-prompt v1|v2|v3|v4` 카드 프롬프트 버전. 기본 `v1`. `v2`/`v3`/`v4`는 대상 원문을 start_quote~end_quote 구간으로 넣고 범위는 케이스가 정합니다(V1 생략). `v4`는 `card_prompt_v4.txt` + `card_schema_v4.txt`.
- `--models`에 `claude-`로 시작하는 이름이 있으면 Anthropic Messages API(`claude_call.py`)를 씁니다. 키는 루트 `.env`의 `ANTHROPIC_API_KEY`.
- `--no-ai-verify` 카드별 AI 검증 호출을 건너뜁니다. 규칙 검사(V6, V8, V9 등)는 그대로 실행합니다.
- `--claude-thinking off|on` Claude Sonnet/Opus 5 thinking. 기본 `off`(요청에 thinking disabled, max_tokens=4096, temperature 생략). `on`이면 thinking을 끄지 않고 max_tokens=16000.
- `--thinking-budget N` Gemini `thinkingConfig.thinkingBudget`. `0`이면 생각 토큰을 끕니다. 빈 응답의 `finishReason`·`usageMetadata`는 `results.json`에 남습니다.
- `--runs 3`과 `--verifier-only`를 같이 쓰면 케이스마다 run1~3 안정성 표를 `report.md`에 넣습니다.

API 키는 프로젝트 루트 `.env`의 `GEMINI_API_KEY`를 그대로 사용합니다. 키 값은 출력·저장하지 않습니다.

## 앱 맞춤법 (`--spell-via-app`)

앱이 켜져 있을 때만 동작합니다. 문단별로 `POST /api/spellcheck`를 `prefer=auto`와 `prefer=public` 둘 다로 호출합니다.

요청 JSON: `{"text": "<문단>", "prefer": "auto"|"public"}`  
응답 JSON: `errors`(각 `original`, `suggestions`, `help`), `error_count`, `provider` 등.

기본 주소:

- `http://127.0.0.1:8766` (`python app.py` / `start_supertory.bat` — 개발 서버)
- 없으면 `http://127.0.0.1:8765` (패키지 exe)

다른 주소를 쓰려면 `--app-url http://127.0.0.1:8766` 을 지정합니다.

앱이 꺼져 있으면 이 항목은 건너뜁니다. 저장소 루트에서 `start_supertory.bat` 또는 `python app.py`로 켠 뒤 다시 실행하세요.

```bash
python tools/prompt_lab/run.py --spell-via-app
```

## 깨끗한 원고 오탐 (`--spell-clean`)

`manuscripts/ep2.md`와 `ep3.md`의 본문 문단을 `prefer=auto`로 전부 검사합니다. 문단 사이에 1초 쉽니다. 429가 반복되면 중단합니다.

```bash
python tools/prompt_lab/run.py --spell-clean
```

결과는 `results/<날짜-시각>/spell_clean.md` 입니다. 다듬은 원고라 제안이 거의 없어야 정상입니다.

## 결과

`tools/prompt_lab/results/<날짜-시각>/`

- `report.md` — 원문 구간, 입력 항목, 모델 결과, 규칙 검사, AI 검증
- `results.json` — 같은 내용의 기계용 기록 (빈 응답이면 `finish_reason`, `usage_metadata`)
- `scoring.md` — 케이스×모델 채점표(빈칸). 0~2를 채운 뒤:

```bash
python tools/prompt_lab/score.py tools/prompt_lab/results/<날짜-시각>/scoring.md
```

## 원고

`manuscripts/`에 `ep2.md`, `ep2x.md`, `ep3.md`, `rofan1.md`가 있어야 합니다. 없는 원고의 케이스는 건너뜁니다.

## 리포트 단계 (`run_report.py`)

첨삭 카드가 아니라 회차 리포트 JSON을 시험합니다. `cases_report.json`의 실행(repeat 포함 기본 7회)을 `claude-sonnet-5`로 호출합니다. 기존 `run.py`와 `prompts/` 카드 템플릿은 건드리지 않습니다.

```bash
python tools/prompt_lab/run_report.py
python tools/prompt_lab/run_report.py --only RP-ep2x,RP-ep2dup
python tools/prompt_lab/run_report.py --max-cost 0.8
```

결과는 `results/<날짜-시각>/` 아래 `report_runs.md`, `report_metrics.md`, `results_report.json`, `cost_summary_report.md` 입니다. 누적 비용이 `--max-cost`를 넘거나 429/오류가 나면 그 지점에서 멈춥니다.

- `--report-prompt v1|v2|v3` 리포트 프롬프트. 기본 `v2`. `v3`는 정합성 검사를 먼저 돌려 기계 검사 결과에 넣습니다.
- `--stage report|consistency` 기본 `report`. `consistency`는 인물·설정 충돌만 뽑습니다.
- `--structured on|off` JSON Schema 구조화 출력(`output_config.format`). 기본 `on`. 스키마를 거부하면 단순화 후, 그래도 안 되면 옵션 없이 호출합니다.
- `--claude-thinking off|on` 기본 `off`(max_tokens 8192). `on`이면 max_tokens 16000.
- `--extra-thinking-on RP-ep2x` 지정 실행을 thinking on으로 한 번 더 돌립니다.

```bash
python tools/prompt_lab/run_report.py --max-cost 1.0 --extra-thinking-on RP-ep2x
```

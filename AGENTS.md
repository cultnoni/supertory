# AGENTS.md — SuperTory

Guidance for AI coding agents working in this repository.

## What this is

**SuperTory** is a **local-only** Korean writing app (Scrivener-like).  
No cloud account, no install step: double-click `start_supertory.bat` → browser at `http://127.0.0.1:8765`.

- **Source of truth for writing:** `supertory.sqlite3`, location decided by `resolve_writable_data_dir()` in `app.py`. **Default for every run mode — `start_supertory.bat`, `python app.py`, dev Electron `npm start`/`electron .`, and the packaged `.exe` — is the shared `%APPDATA%\supertory\data` folder.** Only `SUPERTORY_DB_MODE=isolated` (set by `start_supertory_isolated.bat` / `npm run start:isolated`) points at the repo's own `data/`. Don't assume `python app.py` writes to repo `data/` — check which mode is active before reading/seeding a DB for debugging.
- **External “open this work” handles:** `projects/*.stg` (not the full manuscript)
- **UI:** static files under `web/` served by a small Python HTTP server in `app.py`
- **AI (optional):** Gemini via `.env` (`GEMINI_API_KEY`, AI 패널) and Claude via `.env` (`ANTHROPIC_API_KEY`, 첨삭 피드백 파이프라인) — never commit secrets
- **Cloud sync (optional, login-gated):** Supabase pairing for desktop↔phone (`sync/`), used only if the user signs in from the admin user-management tab. Blank `SUPABASE_URL`/`SUPABASE_ANON_KEY` → fully offline; never make a feature require this.

User-facing docs live in `README.md` (Korean). Prefer matching that product language and tone in UI strings.

---

## Stack

| Layer | Tech |
|--------|------|
| Server / API | Python 3, stdlib `http.server` + SQLite (`app.py`, huge — search before editing) |
| Frontend | Vanilla JS / HTML / CSS (`web/app.js`, `index.html`, `styles.css`) — no bundler |
| Schema | Ordered SQL (and a few `.py` data-migration) files in `db/` |
| Desktop packaging | Electron (`electron/main.js`, `electron-builder` in `package.json`) + `electron-updater` |
| Services / repositories | Newer features (translation, typeset, scene content, conflict resolution) factor logic into `services/*_service.py` over `repositories/*_repository.py` instead of inline handlers in `app.py` — follow that split for new work in those areas, but don't force-migrate older `app.py` handlers as a drive-by |
| AI pipelines | `gemini_client.py` (AI 패널, optional), `feedback_pipeline/` + `feedback_api.py` + `feedback_store.py` (Claude 첨삭/문학 피드백, optional) |
| Cloud sync | `sync/` (Supabase auth/pairing, optional) + `supabase/` (its own migrations, separate from `db/`) |
| Packages | `document_import.py`, `document_export.py`, `project_package.py`, `korean_speller.py`, `env_loader.py`, `folder_tree.py`, `genre_clusters.py` |

Default port: **8765** (`HOST=127.0.0.1`).

---

## Layout

```
app.py                 # HTTP API + static serving + DB init/migrations
web/
  index.html           # Single-page shell
  app.js               # All client logic (large; search before editing)
  styles.css           # All styles
  island.css           # 섬과 종이 design layer, loaded after styles.css
  locales/{ko,en,es}.json
  dev/main-design-v4.html  # Living design mock
db/
  001_initial_schema.sql
  002_….sql / NNN_….py  # Incremental migrations (mostly SQL, a few data-backfill .py)
  README.md
services/              # Business logic for newer features (translation, typeset, scene content, conflicts)
repositories/          # DB access backing services/
feedback_pipeline/     # Claude 첨삭/문학 피드백 runner, CLI (`__main__.py`), prompts/
sync/                  # Optional Supabase auth + desktop↔phone pairing (local writing works without it)
supabase/              # Cloud-side schema migrations for sync/ (separate from db/)
electron/              # Desktop shell: main.js, preload.js, backend launch/stop
scripts/               # Build/release/one-off maintenance scripts (build_backend.py, backfill_*.py, e2e_*.py)
docs/                  # design-guidelines.md, feedback_api.md, literary-pipeline.md
data/                  # Runtime DB + illustrations (gitignored; default location is %APPDATA%\supertory\data, see "What this is")
projects/              # *.stg package files
tests/                 # unittest (+ fixtures/, _qa_*.py smoke scripts)
start_supertory.bat    # Windows launcher (shared AppData DB)
start_supertory_isolated.bat  # Windows launcher against repo data/
```

Do **not** commit: `.env`, `data/*.sqlite3`, illustration binaries, `__pycache__/`, `node_modules/`, `dist/`.

---

## Commands

```bash
# Run app (from repo root) — writes to shared %APPDATA%\supertory\data by default
python app.py

# Run against the repo's own data/ instead (matches start_supertory_isolated.bat)
SUPERTORY_DB_MODE=isolated python app.py

# Dev Electron shell (same default DB location rules as above)
npm start                 # shared AppData
npm run start:isolated    # repo data/

# Schema / unit tests
python -m unittest discover -s tests -v
python -m unittest tests.test_schema -v

# Quick JS syntax check after large app.js edits
node --check web/app.js

# Undefined identifier check (ESLint no-undef; also via unittest)
npm run lint:web
python -m unittest tests.test_web_js_undef -v
```

Windows: `start_supertory.bat` (uses a fixed local Python path in that file) / `start_supertory_isolated.bat` for repo-local data.

---

## Backend conventions

### HTTP API

- JSON under `/api/…` (GET/POST/PUT/DELETE as defined in `app.py`)
- Static UI from `web/` for non-API paths
- Use existing helpers: `database()`, `send_json`, `api_error`, `as_dict`, `require_project`
- Connections: `PRAGMA foreign_keys = ON`, `journal_mode = WAL`

### Migrations

1. Add `db/NNN_descriptive_name.sql` with next version number  
2. Register path + apply block in `initialise_database()` in `app.py` (same pattern as `002`–`014`)  
3. Record version in `schema_migration` inside the migration or apply path  
4. Keep migrations **additive and idempotent** where possible  
5. Extend `tests/test_schema.py` (or related tests) when contracts change  

See `db/README.md` for FTS rebuild notes.

### Domain model (high level)

- **project** → chapters → **scenes** (manuscript units; content is HTML/MD in revisions)
- **characters**, aliases, scene membership / POV, relations (`character_relations.py`)
- **settings docs** on project: synopsis, logline, worldbuilding, intro/intent, keywords, genre, purpose, `literary_form` (단편/장편 for 일반문학/순문학 projects — see `docs/literary-pipeline.md`)
- **ideas** (idea bank sticky notes)
- **feedback_run / feedback_card**: 첨삭 피드백 실행 기록과 카드 (`feedback_pipeline/`, `feedback_api.py`, `feedback_store.py`) — see `docs/feedback_api.md` for status/polling contract
- scene extras: illustrations, reference links, goals, author notes
- writing log / prefs / mobile-related endpoints exist under `/api/writing/…`

Prefer reusing existing serializers and word-count helpers rather than inventing parallel formats.

---

## Frontend conventions

### Structure

- One big SPA: `web/app.js` holds state (`const state = {…}`), API (`api()`), and UI  
- Prefer **existing** patterns: `$("id")`, toast, `handleError`, binder panels, settings boxes  
- IDs in `index.html` are the contract; keep HTML / JS / CSS names aligned  

### Settings codex (설정집)

- Sidebar boxes: `data-settings-section`, toggle + optional **→ 메인에서 보기** (`data-settings-main`)  
- Main views: idea board, keyword board, character board, settings-doc main (`synopsisWorkspace`), etc.  
- When adding “open on main”, register in `SETTINGS_BOOKMARK_META` and in `MAIN_PANES`, then open with `showMainPane(key)` so peer center panes stay consistent

### 디자인 (섬과 종이)

- 화면·기능을 추가하거나 고칠 때는 **`docs/design-guidelines.md`** 를 먼저 읽고 따른다 (토큰, 부품, 기능 배치 지도).
- 디자인 레이어는 `web/island.css` (`styles.css` 뒤 로드). 새 디자인 규칙은 여기에 쓰고, 색은 기존 테마 변수만 쓴다.
- 살아 있는 시안: `web/dev/main-design-v4.html`. 마스코트는 `/assets/tori-mascot.png`만 사용(글자·이모지 대체 금지).
- 새 기능은 지침 §7 배치 지도에 자리를 먼저 정한다 — 화면에서 접근할 수 없는 기능을 만들지 않는다.

### Viewer

Modes: `pdf` | `book` | `phone` | `eink` (settings in `localStorage` under `supertory.viewerSettings`).

- Phone/eink use **device page stack** (`layoutDevicePages` / `paintDevicePageStack`)  
- Page colors use CSS vars `--viewer-page-bg` / `--viewer-page-ink`  
- **Important:** phone mode sets **inline** CSS vars; when switching modes (especially **eink**), clear or re-apply those vars so style presets work (`clearViewerPageColorVars` / `applyViewerPageColors` / `VIEWER_EINK_STYLES`)  
- Do not leave stale `stage.style` color vars when changing viewer mode  

### UI copy

- Default product language is **Korean**; **English** and **Spanish** must stay in sync  
- Any new or changed user-visible string goes in `web/locales/ko.json`, `en.json`, and `es.json` in the same change (same key)  
- Wire HTML via `data-i18n` / `data-i18n-html` / `data-i18n-placeholder` / `data-i18n-title`; do not leave Korean-only copy in `index.html` or `app.js`  
- Placeholders and tooltips should stay short and consistent with nearby strings  
- Prefer real `<textarea placeholder="…">` behavior; avoid CSS that hides placeholders on `:focus` for empty fields unless intentional  

### Persistence on client

Many UX prefs use `localStorage` keys prefixed `supertory.` (theme, ink, viewer, baits, bookmarks, binder width, open settings section, etc.). Don’t rename keys lightly.

---

## Product areas (where to look)

| Feature | Primary files |
|---------|----------------|
| Scene editor, autosave, split view | `web/app.js`, scene workspace in `index.html` |
| Binder / outline | `web/app.js` outline render + `app.py` outline APIs |
| Settings (keywords, world, intro…) | settings accordion + `/api/projects/{id}/settings` |
| Idea bank | ideas APIs + `ideaBoard` |
| Viewer (PDF/book/phone/eink) | viewer modal + `VIEWER_*` in `app.js` / CSS |
| Import / export | `document_import.py`, `document_export.py`, import UI |
| 교정고 → 회차 매칭 | `chapter_match.py`, `POST /api/projects/{id}/match-episode`, import destination `match_replace_scene` |
| 교정/교열 비교 보고서 | `proof_diff.py`, `POST /api/projects/{id}/proof-diff`, import destination `proof_compare` |
| HWP 교정고 본문 정제 | `proof_clean.py`, `POST /api/proof-clean`, auto-used on match_replace / proof-diff |
| HWP/DOCX 통합 추출·파이프라인 | `proof_extract.py` (pyhwp / python-docx), `proof_pipeline.py`, `POST /api/projects/{id}/proof-pipeline`, import `proof_pipeline` · deps in `requirements-proof.txt` |
| `.stg` packages | `project_package.py` |
| Gemini AI panel | `gemini_client.py`, SuperTORY UI |
| 첨삭 피드백 파이프라인 (웹소설/문학) | `feedback_pipeline/`, `feedback_api.py`, `feedback_store.py`, `docs/feedback_api.md`, `docs/literary-pipeline.md` |
| 번역(번역 작업/용어/세그먼트) | `services/translation_*_service.py`, `repositories/translation_*_repository.py`, `translation_prompts.py`, `translation_context.py` |
| 조판 내보내기 (플랫폼별 DOCX) | `typeset_export.py`, `services/typeset_service.py`, `repositories/typeset_repository.py` |
| 데스크톱 동기화/페어링 (선택) | `sync/`, `supabase/`, admin user-management 탭에서 로그인해야 활성화 |
| Electron 패키징 / 자동 업데이트 | `electron/main.js`, `electron-builder` 설정(`package.json`), `scripts/build_backend.py`, `update-feed.json` |
| Spellcheck | `korean_speller.py` |
| Illustrations | `data/illustrations/`, illustration APIs |

---

## Testing expectations

- Prefer **unittest** under `tests/`  
- Use temp DB / isolated `DATA_DIR` patterns already in tests — don’t point tests at the user’s real shared-AppData or repo `data/supertory.sqlite3`  
- After schema or package changes, run at least `test_schema` and the suite closest to the change  
- `tests/_qa_*.py` are manual smoke scripts, not part of `unittest discover` — run them directly when touching the areas they cover  
- No frontend test runner; smoke-check in browser after UI work  

---

## Do / Don’t

**Do**

- Keep writing **local-first**; don’t add required cloud backends — `sync/`/`supabase/` stay opt-in behind sign-in, never a precondition for editing a scene  
- **Manuscript safety:** on every edit, snapshot scene content to device storage (`writeLocalSceneDraftSync` / IndexedDB) **before** network `PUT`. API/network failures must never wipe the editor; quiet autosave should keep local drafts and retry  
- Match existing Korean UI wording and interaction patterns  
- Clear conflicting view state when opening a new main pane (scene / character / idea / keyword / synopsis / viewer)  
- Add DB migrations for new persisted fields; don’t only store important manuscript data in `localStorage`  

**Don’t**

- Commit `.env`, API keys, or user `data/` databases  
- Introduce a heavy frontend framework/build step without an explicit request  
- Break `.stg` “open this project” behavior or migrate away from SQLite without a plan  
- “Fix” viewer/page colors only in CSS while phone mode still leaves overriding inline CSS variables  
- Clear `sceneDirty` or local drafts after a failed server save  
- `throw` away autosave errors without first ensuring a local draft exists  

---

## 영어·스페인어권 출시 전 전체 언어 검토 (필수)

**에이전트가 먼저 알려야 한다.** 사용자가 영어권·스페인어권 출시, 해외 광고·홍보, 스토어 등록, 글로벌 런칭 같은 말을 꺼내면(“곧 영어랑 스페인어권에도 광고 뿌리고 출시 준비할 거야” 등), 묻기 전에 이 검토가 아직 안 끝났다는 것과 아래 점검 목록을 먼저 알리고 진행 여부를 묻는다. 검토를 마치기 전에는 영어·스페인어를 “출시 준비 완료”라고 말하지 않는다.

**현재 상태 (2026-10-23 기준)**
- `web/locales/en.json`, `es.json`은 키 5,840개가 `ko.json`과 맞지만, 번역은 전부 AI가 만들었고 원어민이 본 적이 없다. 값에 한글이 남은 곳 4개, 한국어와 같은 값 18개가 있다(출시 전에 모두 확인).
- `web/samples/en.json`, `es.json`(첫 실행 예시 작품)은 독립 AI 검토자 4회만 거쳤다. 원어민 확인은 없다. 한국어 웹소설 원문을 번역한 것이라 문체가 어색할 수 있다.
- 사용자가 원어민이 아니라서 직접 눈으로 확인할 수 없다. 반드시 다른 수단(원어민 검수자, 독립 검토 에이전트, 기계 검사)으로 확인하고 결과를 보고한다.

**점검 목록**
1. **로케일 전체 문장:** `en.json`/`es.json` 5,840개 — 문법, 자연스러움, 용어 일관성(토리, 떡밥, 회차, 설정집 등), `${...}` 자리표시자, UI 길이, 스페인어는 변종(중남미 중립 여부)과 tú/usted 기준.
2. **로케일 밖에 박힌 한국어:** `app.py`의 사용자에게 보이는 오류·안내 문구(`ValueError` 메시지 등), `electron/main.js`의 대화상자·설치·자동 업데이트 문구, `feedback_api.py`와 `feedback_pipeline/` 프롬프트·결과 언어, `web/index.html`·`web/app.js`에 직접 쓴 한글, 안내 투어(driver.js)·시작 화면 문구, `update-feed.json` 공지.
3. **예시 작품:** `web/samples/en.json`, `es.json` — 원어민 확인, 저작권 만료 원문의 출처 표기, 번역 웹소설의 문체.
4. **문서·배포물:** `README.md`(한국어뿐)의 영어/스페인어판, 스토어 설명, 개인정보·약관 등 언어별 필요 여부.
5. **서식:** 날짜·숫자·통화·상대 시간, 제목용 부분 폰트(`web/fonts/GowunBatang-Bold-title.ttf`)에 새 제목에 쓰는 글자가 모두 들어 있는지.

**검토 방법과 주의**
- 별도 브랜치에서 하고 PR로 올린다. ko/en/es 키는 항상 같이 맞춘다.
- 독립 검토자(읽기 전용)를 쓰되, “인용문 || 위치”처럼 필드를 한 줄로 합쳐 주면 오해하니 필드를 따로 보여 준다. 자동 검사(원문과의 한 글자 대조, 떡밥 인용문이 실제 본문에 있는지, 따옴표·악센트 짝 맞춤)를 함께 돌린다.
- 옛 철자를 현재 철자로 바꿀 때 `replace("dió","dio")`처럼 낱말 일부에 걸리게 하지 않는다(`comprendió`가 망가진 적이 있다). 반드시 단어 경계로 바꾸고 원문과 달라진 낱말을 목록으로 확인한다.
- 이 검토는 원어민 사람의 확인을 대체하지 못한다. 출시 전에는 사람 원어민 검수를 권한다.

---

## Change checklist (PR-style)

1. Scope is clear (API / migration / UI only what was asked)  
2. New columns → migration + `app.py` apply + tests if contract-facing  
3. New settings main view → HTML + JS open/close + hide peers + CSS  
4. Viewer color/style → apply and clear CSS vars per mode  
5. `python -m unittest discover -s tests -v` (or targeted tests)  
6. Front-end JS changed → `npm run lint:web` (or `python -m unittest tests.test_web_js_undef`) and `node --check web/app.js`  
7. Manual smoke: open scene, settings box, viewer mode if touched  

---

## User communication

- Repo UI strings live in `web/locales/{ko,en,es}.json` (keep all three in sync); `README.md` is Korean; always respond to the user in Korean (한국어), even when the user pastes English text or the work involves English/Spanish strings  
- Keep explanations short: what broke, what you changed, how to verify  
- Don’t invent product features that contradict local-only / SQLite design  

When unsure, read neighboring code in `web/app.js` or the matching `tests/test_*.py` before inventing a new pattern.

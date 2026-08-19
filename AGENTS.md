# AGENTS.md — SuperTory

Guidance for AI coding agents working in this repository.

## What this is

**SuperTory** is a **local-only** Korean writing app (Scrivener-like).  
No cloud account, no install step: double-click `start_supertory.bat` → browser at `http://127.0.0.1:8765`.

- **Source of truth for writing:** `data/supertory.sqlite3`
- **External “open this work” handles:** `projects/*.stg` (not the full manuscript)
- **UI:** static files under `web/` served by a small Python HTTP server in `app.py`
- **AI (optional):** Gemini via `.env` (`GEMINI_API_KEY`) — never commit secrets

User-facing docs live in `README.md` (Korean). Prefer matching that product language and tone in UI strings.

---

## Stack

| Layer | Tech |
|--------|------|
| Server / API | Python 3, stdlib `http.server` + SQLite (`app.py`) |
| Frontend | Vanilla JS / HTML / CSS (`web/app.js`, `index.html`, `styles.css`) — no bundler |
| Schema | Ordered SQL migrations in `db/` |
| Packages | `document_import.py`, `document_export.py`, `project_package.py`, `gemini_client.py`, `korean_speller.py`, `env_loader.py` |

Default port: **8765** (`HOST=127.0.0.1`).

---

## Layout

```
app.py                 # HTTP API + static serving + DB init/migrations
web/
  index.html           # Single-page shell
  app.js               # All client logic (large; search before editing)
  styles.css           # All styles
db/
  001_initial_schema.sql
  002_….sql …          # Incremental migrations
  README.md
data/                  # Runtime DB + illustrations (gitignored)
projects/              # *.stg package files
tests/                 # unittest
start_supertory.bat   # Windows launcher
```

Do **not** commit: `.env`, `data/*.sqlite3`, illustration binaries, `__pycache__/`.

---

## Commands

```bash
# Run app (from repo root)
python app.py

# Schema / unit tests
python -m unittest discover -s tests -v
python -m unittest tests.test_schema -v

# Quick JS syntax check after large app.js edits
node --check web/app.js
```

Windows: `start_supertory.bat` (uses a fixed local Python path in that file).

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
- **characters**, aliases, scene membership / POV  
- **settings docs** on project: synopsis, logline, worldbuilding, intro/intent, keywords, genre, purpose  
- **ideas** (idea bank sticky notes)  
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
- When adding “open on main”, register in `SETTINGS_BOOKMARK_META` and hide other center panes consistently (`ideaBoardOpen`, `keywordBoardOpen`, `characterBoardOpen`, synopsis main)

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
| Spellcheck | `korean_speller.py` |
| Illustrations | `data/illustrations/`, illustration APIs |

---

## Testing expectations

- Prefer **unittest** under `tests/`  
- Use temp DB / isolated `DATA_DIR` patterns already in tests — don’t point tests at the user’s real `data/supertory.sqlite3`  
- After schema or package changes, run at least `test_schema` and the suite closest to the change  
- No frontend test runner; smoke-check in browser after UI work  

---

## Do / Don’t

**Do**

- Keep writing **local-first**; don’t add required cloud backends  
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

## Change checklist (PR-style)

1. Scope is clear (API / migration / UI only what was asked)  
2. New columns → migration + `app.py` apply + tests if contract-facing  
3. New settings main view → HTML + JS open/close + hide peers + CSS  
4. Viewer color/style → apply and clear CSS vars per mode  
5. `python -m unittest discover -s tests -v` (or targeted tests)  
6. `node --check web/app.js` if `app.js` changed  
7. Manual smoke: open scene, settings box, viewer mode if touched  

---

## User communication

- Repo UI strings live in `web/locales/{ko,en,es}.json` (keep all three in sync); `README.md` is Korean; respond to the user in the language they use  
- Keep explanations short: what broke, what you changed, how to verify  
- Don’t invent product features that contradict local-only / SQLite design  

When unsure, read neighboring code in `web/app.js` or the matching `tests/test_*.py` before inventing a new pattern.

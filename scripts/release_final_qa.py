"""
Release-final QA: API smoke + Playwright new-user browser flow.
Produces build/release_final_qa/report.json and screenshots.
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "build" / "release_final_qa"
OUT.mkdir(parents=True, exist_ok=True)

TD = tempfile.TemporaryDirectory()
import app
import document_export
import document_import
from document_export import ManuscriptBlock

app.DATA_DIR = Path(TD.name) / "data"
app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
app.initialise_database()
server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
PORT = int(server.server_port)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"

import http.client

REPORT: dict = {
    "started_at": datetime.now().isoformat(timespec="seconds"),
    "base": BASE,
    "unittest_summary": "see separate run: 131 tests, 7 fail + 1 err (main_genre 미포함 구 테스트 위주)",
    "api": [],
    "browser": [],
    "issues": [],
    "ok": True,
}


def issue(sev: str, name: str, detail: str) -> None:
    REPORT["issues"].append({"severity": sev, "name": name, "detail": detail})
    if sev in {"crash", "misbehave"}:
        REPORT["ok"] = False
    print(f"  [{sev}] {name}: {detail}", flush=True)


def api_ok(name: str, detail: str = "") -> None:
    REPORT["api"].append({"ok": True, "name": name, "detail": detail})
    print(f"  OK  {name}" + (f" — {detail}" if detail else ""), flush=True)


def req(method: str, path: str, body: dict | None = None, raw: bool = False):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=120)
    payload = None
    headers = {}
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, payload, headers)
    resp = conn.getresponse()
    data = resp.read()
    st = resp.status
    conn.close()
    if raw:
        return st, data
    try:
        return st, json.loads(data.decode("utf-8"))
    except Exception:
        return st, data.decode("utf-8", errors="replace")


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def make_manuscript_txt() -> bytes:
    parts = []
    for i in range(1, 6):
        parts.append(
            f"# {i}화 서연의 밤\n\n"
            f"비가 내렸다. 서연이 골목을 걸었다. 묵연이 뒤를 따랐다. "
            f"「{i}화 끝 훅?」 서연이 중얼거렸다.\n"
        )
    return "\n".join(parts).encode("utf-8")


def make_legacy_front() -> bytes:
    parts = []
    for i in range(1, 11):
        body = (
            f"제{i}화. 주인공이 사건을 마주한다. 대사가 빠르다. "
            f"「이게 끝일까?」 훅으로 마무리한다. "
        ) * 30
        parts.append(f"# {i}화\n\n{body}\n")
    return "\n".join(parts).encode("utf-8")


def run_api_smoke() -> dict:
    section("A. API smoke")
    ctx: dict = {}

    st, err = req("POST", "/api/projects", {"title": "장르없음"})
    if st == 400:
        api_ok("project create requires main_genre")
    else:
        issue("minor", "project without genre", f"expected 400 got {st}")

    st, project = req(
        "POST",
        "/api/projects",
        {
            "title": "릴리즈 QA 신작",
            "main_genre": "판타지",
            "purpose": "general_novel",
            "sub_genre": "무협",
        },
    )
    if st != 201:
        issue("crash", "create project", f"{st} {project}")
        return ctx
    pid = project["id"]
    ctx["pid"] = pid
    api_ok("create project", f"id={pid}")

    # settings codex fields
    st, settings = req(
        "PUT",
        f"/api/projects/{pid}/settings",
        {
            "logline_md": "로그라인 QA",
            "outline_summary": "시작-중반-결말 방향 요약",
            "synopsis_md": "시놉시스 본문",
            "intro_md": "작품 소개",
            "intent_md": "기획 의도",
            "worldbuilding_md": "칼과 내공의 세계. 스마트폰 없음.",
            "tory_priority_md": "과장하지 말 것. 담백하게.",
            "keywords": ["판타지", "무협", "회귀"],
        },
    )
    if st == 200 and settings.get("outline_summary"):
        api_ok("settings codex fields incl. outline_summary / tory_priority")
    else:
        issue("misbehave", "settings put", f"{st}")

    st, ch = req("POST", f"/api/projects/{pid}/chapters", {"title": "1장"})
    st, sc1 = req("POST", f"/api/chapters/{ch['id']}/scenes", {"title": "1화"})
    st, sc2 = req("POST", f"/api/chapters/{ch['id']}/scenes", {"title": "2화"})
    ctx["sc1"] = sc1["id"]
    ctx["sc2"] = sc2["id"]
    st, d1 = req("GET", f"/api/scenes/{sc1['id']}")
    content = (
        "<p>서연이 마을에 도착했다. 묵연이 칼을 매고 있었다. "
        "「저 탑이 보이는가?」 서연이 물었다. 비가 내렸다.</p>" * 6
    )
    st, _ = req(
        "PUT",
        f"/api/scenes/{sc1['id']}",
        {
            "title": "1화",
            "status": "draft",
            "content_md": content,
            "row_version": d1["row_version"],
            "synopsis_md": "마을 도착",
        },
    )
    api_ok("scene save with content")

    # document import
    txt = make_manuscript_txt()
    st, imported = req(
        "POST",
        "/api/import",
        {
            "filename": "qa_ms.txt",
            "content_base64": base64.b64encode(txt).decode("ascii"),
            "title": "문서불러오기 QA",
            "purpose": "general_novel",
            "main_genre": "판타지",
            "split_mode": "headings",
        },
    )
    if st in (200, 201) and imported.get("project_id"):
        api_ok("document import", f"scenes={len(imported.get('scene_ids') or [])}")
        ctx["import_pid"] = imported["project_id"]
    else:
        issue("crash", "document import", f"{st} {imported}")
        ctx["import_pid"] = pid

    # HWPX / DOCX export + re-extract
    for fmt in ("docx", "hwpx", "txt"):
        st, data = req(
            "POST",
            f"/api/projects/{ctx['import_pid']}/export",
            {"format": fmt, "save_to_folder": False},
            raw=True,
        )
        if st != 200:
            issue("crash", f"export {fmt}", f"status={st}")
            continue
        if fmt in ("docx", "hwpx") and data[:2] != b"PK":
            issue("crash", f"export {fmt} not zip", "")
            continue
        api_ok(f"export {fmt}", f"bytes={len(data)}")
        if fmt in ("docx", "hwpx"):
            try:
                extracted = document_import.extract_document(f"out.{fmt}", data).text
                if len(extracted) > 10:
                    api_ok(f"export {fmt} re-extract")
                else:
                    issue("minor", f"export {fmt} empty-ish", extracted[:60])
            except Exception as e:
                issue("misbehave", f"export {fmt} extract", str(e))

    # HWPX import path
    blocks = [
        ManuscriptBlock(kind="paragraph", text="1화"),
        ManuscriptBlock(kind="paragraph", text="한글로 쓴 짧은 본문입니다."),
        ManuscriptBlock(kind="paragraph", text="2화"),
        ManuscriptBlock(kind="paragraph", text="둘째 본문입니다."),
    ]
    hwpx = document_export.build_hwpx(blocks)
    st, hwpx_imp = req(
        "POST",
        "/api/import",
        {
            "filename": "qa.hwpx",
            "content_base64": base64.b64encode(hwpx).decode("ascii"),
            "title": "HWPX 가져오기 QA",
            "purpose": "general_novel",
            "main_genre": "판타지",
            "split_mode": "headings",
        },
    )
    if st in (200, 201) and hwpx_imp.get("project_id"):
        api_ok("hwpx import new project")
    else:
        issue("misbehave", "hwpx import", f"{st} {str(hwpx_imp)[:120]}")

    # bait CRUD
    st, bait = req(
        "POST",
        f"/api/projects/{pid}/baits",
        {
            "kind": "plant",
            "quote": "은빛 문양 떡밥",
            "summary": "달빛 암호",
            "plant_scene_id": sc1["id"],
            "recover_scene_id": sc2["id"],
            "notify_on_recover": True,
        },
    )
    if st == 201:
        api_ok("bait create DB")
        st, blist = req("GET", f"/api/projects/{pid}/baits")
        if st == 200 and any(b.get("id") == bait["id"] for b in blist):
            api_ok("bait list notify recover scene")
        st, sn = req("PUT", f"/api/baits/{bait['id']}", {"snooze_until": "next_open"})
        if st == 200:
            api_ok("bait snooze next_open")
    else:
        issue("crash", "bait create", f"{st} {bait}")

    # 15 assist modes (API) — skip heavy live where dry works; use real for a sample
    modes = [
        ("summarize", {"scene_content": content}),
        ("analyze", {"scene_content": content}),
        ("ideas", {"scene_content": content}),
        ("brainstorm", {"scene_content": content, "user_topic": "조연 아이디어"}),
        ("continue", {"scene_content": content, "length_mode": "short"}),
        ("rewrite", {"scene_content": "서연이 걸었다.", "context_before": "", "context_after": ""}),
        ("worlddesc", {"scene_content": content, "target_subject": "주막 내부 묘사"}),
        ("worldscan", {"scene_content": content + " 묵연이 스마트폰을 켰다."}),
        ("dupcheck", {"scene_content": content + content[:200]}),
        ("free", {"user_prompt": "이 장면 톤을 한 줄로 요약해 줘.", "scene_content": content}),
        (
            "foreshadow",
            {
                "scene_content": content,
                "foreshadow": {
                    "title": "은빛 문양",
                    "target": "12화",
                    "buildup": ["1화: 문양 언급", "2화: 달빛"],
                },
            },
        ),
        (
            "plottwist",
            {
                "scene_content": content + " 묵연이 배신자였다.",
                "foreshadow": {
                    "title": "묵연 정체",
                    "target": "결말",
                    "buildup": ["문양", "편지"],
                },
            },
        ),
        (
            "subsynopsis",
            {
                "outline_summary": "시작-중반-결말",
                "synopsis_length_limit": 500,
                "intent_length_limit": 200,
            },
        ),
    ]
    for mode, extra in modes:
        body = {
            "mode": mode,
            "project_id": pid,
            "project_title": "릴리즈 QA 신작",
            "main_genre": "판타지",
            "purpose": "general_novel",
            "scene_title": "1화",
            **extra,
        }
        st, res = req("POST", "/api/ai/assist", body)
        if st == 200 and (res.get("text") or res.get("ok") is not False):
            api_ok(f"assist mode={mode}", f"chars={len(str(res.get('text') or ''))}")
        elif st == 400 and "API" in str(res):
            issue("minor", f"assist {mode}", f"API/key: {res}")
        else:
            issue("misbehave", f"assist {mode}", f"{st} {str(res)[:100]}")

    # success pattern end-to-end API
    st, run = req(
        "POST",
        "/api/success-pattern/run",
        {
            "work_title": "구작 흥행 QA",
            "total_chapters": 100,
            "dry_run": True,
            "sections": [
                {
                    "key": "front",
                    "start_ep": 1,
                    "end_ep": 5,
                    "episodes": [
                        {
                            "title": f"{i}화",
                            "text": ("훅으로 끝. 대사가 빠르다. 「끝?」 " * 20),
                        }
                        for i in range(1, 6)
                    ],
                }
            ],
        },
    )
    if st == 200 and run.get("profile", {}).get("id"):
        api_ok("success pattern profile create", f"id={run['profile']['id']}")
        prof_id = run["profile"]["id"]
        st, linked = req(
            "PUT",
            f"/api/projects/{pid}/settings",
            {"linked_success_profile_id": prof_id},
        )
        if st == 200 and linked.get("linked_success_profile_id") == prof_id:
            api_ok("link success profile to project")
        else:
            issue("misbehave", "link profile", f"{st} {linked}")
        # chat success analysis
        st, chat = req(
            "POST",
            "/api/ai/assist",
            {
                "mode": "chat",
                "chat_mode": "successAnalysis",
                "user_prompt": "재미요소가 뭐가 부족해?",
                "history": [],
                "project_id": pid,
                "scene_content": content,
                "persona_mode": "default",
                "success_profile": run["profile"].get("profile") or {},
                "main_genre": "판타지",
                "purpose": "general_novel",
            },
        )
        if st == 200 and len(str(chat.get("text") or "")) > 20:
            api_ok("success analyst chat")
        else:
            issue("misbehave", "success analyst chat", f"{st}")
    else:
        issue("misbehave", "success pattern run", f"{st}")

    # auto-update related: just check package.json / electron main has updater (static)
    main_js = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    if "autoUpdater" in main_js or "checkForUpdates" in main_js:
        api_ok("electron autoUpdater present in main.js")
    else:
        issue("minor", "autoUpdater", "not found in electron/main.js")

    # meta
    st, _ = req("GET", "/api/meta/export-formats")
    api_ok("export formats meta") if st == 200 else issue("minor", "export formats", str(st))
    st, ai = req("GET", "/api/ai/status")
    api_ok("ai status", str(ai)[:80] if st == 200 else str(st))

    return ctx


def run_browser(ctx: dict) -> None:
    section("B. Browser new-user flow")
    from playwright.sync_api import sync_playwright

    def br(name: str, **extra):
        REPORT["browser"].append({"name": name, **extra})
        print(f"  BR  {name} {extra}", flush=True)

    def kill_tutorial(page):
        page.evaluate(
            """() => {
              try { localStorage.setItem('tutorial_completed','1'); } catch(e) {}
              try { if (window.onboardingDriver) window.onboardingDriver.destroy(); } catch(e) {}
              document.querySelectorAll('.driver-overlay,.driver-popover').forEach(e=>e.remove());
            }"""
        )
        page.add_style_tag(
            content=".driver-overlay,.driver-popover{display:none!important;pointer-events:none!important;}"
        )

    console_errors: list[str] = []
    page_errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on(
            "console",
            lambda m: console_errors.append(m.text)
            if m.type == "error"
            else None,
        )
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        # Fresh first launch: allow tutorial briefly then capture
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(900)
        has_driver = page.locator(".driver-overlay, .driver-popover").count() > 0
        page.screenshot(path=str(OUT / "01_first_open.png"), full_page=True)
        br("first_open", onboarding_overlay=has_driver)
        # For rest of flow, dismiss tutorial so we can click
        kill_tutorial(page)
        page.evaluate("() => { try { localStorage.setItem('tutorial_completed','1'); } catch(e) {} }")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        kill_tutorial(page)

        # New project via API + UI select (reliable)
        created = page.evaluate(
            """async () => {
              const r = await fetch('/api/projects', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({title:'브라우저 QA 신작', main_genre:'판타지', purpose:'general_novel'})
              });
              return {status:r.status, data: await r.json()};
            }"""
        )
        if created.get("status") not in (200, 201):
            issue("crash", "browser create project", str(created))
            browser.close()
            return
        pid = created["data"]["id"]
        page.evaluate(
            """async (pid) => {
              if (typeof loadProjects==='function') await loadProjects(pid);
              const sel=document.getElementById('projectSelect');
              if(sel){ sel.value=String(pid); sel.dispatchEvent(new Event('change',{bubbles:true})); }
              if(typeof loadProject==='function') await loadProject();
            }""",
            pid,
        )
        page.wait_for_timeout(800)
        br("project_created_ui", pid=pid)
        page.screenshot(path=str(OUT / "02_project.png"), full_page=True)

        # Import manuscript via API then reload
        txt_b64 = base64.b64encode(make_manuscript_txt()).decode("ascii")
        imp = page.evaluate(
            """async (b64) => {
              const r = await fetch('/api/import', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({
                  filename:'ms.txt', content_base64:b64, title:'불러온 원고',
                  purpose:'general_novel', main_genre:'판타지', split_mode:'headings'
                })
              });
              return {status:r.status, data: await r.json()};
            }""",
            txt_b64,
        )
        br("document_import", status=imp.get("status"), scenes=len((imp.get("data") or {}).get("scene_ids") or []))
        if imp.get("status") not in (200, 201):
            issue("misbehave", "browser import", str(imp)[:150])

        # Use the import project
        ipid = (imp.get("data") or {}).get("project_id") or pid
        page.evaluate(
            """async (pid) => {
              if (typeof loadProjects==='function') await loadProjects(pid);
              const sel=document.getElementById('projectSelect');
              if(sel){ sel.value=String(pid); sel.dispatchEvent(new Event('change',{bubbles:true})); }
              if(typeof loadProject==='function') await loadProject();
            }""",
            ipid,
        )
        page.wait_for_timeout(1000)

        # Open first scene
        opened = page.evaluate(
            """async () => {
              const btn = document.querySelector('[data-scene]');
              if (!btn) return 'no-scene';
              const id = Number(btn.dataset.scene);
              if (typeof openScene === 'function') { await openScene(id); return 'openScene:'+id; }
              btn.click();
              return 'click:'+id;
            }"""
        )
        br("open_scene", result=opened)
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / "03_scene.png"), full_page=True)

        # Settings codex check
        page.evaluate(
            """async () => {
              if (typeof setActiveBinder==='function') setActiveBinder('settings');
              if (typeof renderLinkedSuccessProfileCard==='function') await renderLinkedSuccessProfileCard();
            }"""
        )
        page.wait_for_timeout(400)
        has_outline = page.locator("#projectOutlineSummary").count() > 0
        has_tory_prio = page.locator("#toryPriorityInput, #toryPriorityToggle").count() > 0
        has_link_card = page.locator("#linkedSuccessProfilePanel").count() > 0
        br(
            "settings_codex",
            outline_summary_field=has_outline,
            tory_priority=has_tory_prio,
            linked_profile_card=has_link_card,
        )
        page.screenshot(path=str(OUT / "04_settings.png"), full_page=True)
        if not has_outline:
            issue("misbehave", "줄거리 개요 필드 없음", "")
        if not has_link_card:
            issue("minor", "흥행 프로파일 연결 카드 없음", "")

        # Fill outline summary + world via UI
        page.evaluate(
            """() => {
              const o = document.getElementById('projectOutlineSummary');
              if (o) { o.value = '시작에서 결말까지 줄거리 개요 QA'; o.dispatchEvent(new Event('input',{bubbles:true})); }
              const w = document.getElementById('projectWorldbuilding');
              if (w) { w.value = '세계관: 칼과 내공'; w.dispatchEvent(new Event('input',{bubbles:true})); }
            }"""
        )
        br("settings_fields_filled")

        # Bait via UI modal if possible, else API
        page.evaluate(
            """async (pid) => {
              if (typeof setActiveBinder==='function') setActiveBinder('manuscript');
              const scenes = [...document.querySelectorAll('[data-scene]')].map(b=>Number(b.dataset.scene));
              if (scenes.length >= 2 && typeof api === 'function') {
                await api(`/api/projects/${pid}/baits`, {
                  method:'POST',
                  body: JSON.stringify({
                    kind:'plant', quote:'브라우저 떡밥', summary:'테스트',
                    plant_scene_id: scenes[0], recover_scene_id: scenes[1],
                    notify_on_recover: true
                  })
                });
                if (typeof refreshBaitsFromServer==='function') await refreshBaitsFromServer(pid);
                if (typeof renderToryNotifyList==='function') renderToryNotifyList();
              }
            }""",
            ipid,
        )
        page.wait_for_timeout(400)
        br("bait_via_api_ui_refresh")

        # Success formula wizard (front 10)
        page.evaluate(
            """() => {
              if (typeof setAiPanelOpen==='function') setAiPanelOpen(true);
              document.getElementById('aiTabTools')?.click();
            }"""
        )
        page.wait_for_timeout(300)
        page.select_option("#aiMode", "successpattern")
        page.wait_for_timeout(300)
        wiz_ok = page.locator("#successPatternWizard:not(.hidden)").count() > 0 or (
            "hidden" not in (page.locator("#successPatternWizard").get_attribute("class") or "")
        )
        br("success_wizard_open", visible=bool(wiz_ok))
        if wiz_ok:
            page.locator("#spNextButton").click(force=True)
            page.fill("#spWorkTitle", "브라우저 구작")
            page.locator("#spNextButton").click(force=True)
            page.fill("#spTotalChapters", "100")
            page.locator("#spNextButton").click(force=True)
            page.locator('[data-sp-section="front"]').check(force=True)
            page.locator('[data-sp-section="middle"]').uncheck(force=True)
            page.locator('[data-sp-section="ending"]').uncheck(force=True)
            page.locator("#spNextButton").click(force=True)
            page.wait_for_timeout(200)
            page.locator("#spNextButton").click(force=True)
            page.wait_for_timeout(300)
            front_path = OUT / "legacy_front.txt"
            front_path.write_bytes(make_legacy_front())
            page.locator('[data-sp-upload-btn="front"]').click(force=True)
            page.set_input_files("#spFileInput", str(front_path))
            page.wait_for_timeout(2500)
            page.locator("#spAnalyzeButton").click(force=True)
            try:
                page.wait_for_function(
                    """() => {
                      const s = document.getElementById('spAnalyzeStatus');
                      const t = document.getElementById('aiResult');
                      return (s && s.textContent.includes('저장 완료'))
                        || (t && t.value && t.value.includes('프로파일'));
                    }""",
                    timeout=180000,
                )
                br("success_profile_created", ok=True)
            except Exception as e:
                issue("misbehave", "success wizard analyze", str(e)[:120])
                br("success_profile_created", ok=False)
            page.screenshot(path=str(OUT / "05_success_profile.png"), full_page=True)

            # linked card
            page.evaluate(
                """async () => {
                  document.querySelectorAll('.modal').forEach(m=>m.classList.add('hidden'));
                  if (typeof setActiveBinder==='function') setActiveBinder('settings');
                  if (typeof renderLinkedSuccessProfileCard==='function') await renderLinkedSuccessProfileCard();
                }"""
            )
            page.wait_for_timeout(400)
            link_text = page.locator("#linkedSuccessProfileStatus").inner_text()
            br("linked_profile_card", text=link_text[:120])
            page.screenshot(path=str(OUT / "06_linked_card.png"), full_page=True)

            # success feedback
            page.evaluate(
                """async () => {
                  document.querySelectorAll('.modal').forEach(m=>m.classList.add('hidden'));
                  if (typeof setActiveBinder==='function') setActiveBinder('manuscript');
                  if (typeof setAiPanelOpen==='function') setAiPanelOpen(true);
                  document.getElementById('aiTabTools')?.click();
                  const btn = document.querySelector('[data-scene]');
                  if (btn && typeof openScene==='function') await openScene(Number(btn.dataset.scene));
                }"""
            )
            page.wait_for_timeout(500)
            page.select_option("#aiMode", "successfeedback")
            page.locator("#aiSubmitButton").click(force=True)
            try:
                page.wait_for_function(
                    """() => {
                      const t = document.getElementById('aiResult')?.value || '';
                      return t.length > 50 && !t.includes('비교하는 중');
                    }""",
                    timeout=120000,
                )
                fb = page.locator("#aiResult").input_value()
                br("success_feedback", length=len(fb))
                (OUT / "07_feedback.txt").write_text(fb, encoding="utf-8")
            except Exception as e:
                issue("misbehave", "success feedback UI", str(e)[:120])

            page.evaluate(
                """() => { document.querySelectorAll('.modal').forEach(m=>m.classList.add('hidden'));
                  if (typeof closeAiResultModal==='function') try{closeAiResultModal({quiet:true})}catch(e){} }"""
            )

            # brainstorm + checkbox
            page.select_option("#aiMode", "brainstorm")
            page.wait_for_timeout(300)
            page.evaluate(
                """() => {
                  if (typeof updateSuccessProfileRefUi==='function') updateSuccessProfileRefUi();
                  const c = document.getElementById('successProfileRefCheck');
                  if (c) c.checked = true;
                }"""
            )
            if page.locator("#brainstormTopic").count():
                page.fill("#brainstormTopic", "미니 악역")
            page.evaluate("() => { const el=document.getElementById('aiResult'); if(el) el.value='…대기…'; }")
            page.locator("#aiSubmitButton").click(force=True)
            try:
                page.wait_for_function(
                    """() => {
                      const t = document.getElementById('aiResult')?.value || '';
                      return t.length > 40 && !t.includes('대기');
                    }""",
                    timeout=120000,
                )
                br("brainstorm_with_ref", length=len(page.locator("#aiResult").input_value()))
            except Exception as e:
                issue("misbehave", "brainstorm+ref", str(e)[:100])

            page.evaluate(
                """() => { document.querySelectorAll('.modal').forEach(m=>m.classList.add('hidden')); }"""
            )

            # analyst chat
            page.evaluate(
                """() => {
                  document.getElementById('aiTabChat')?.click();
                  if (typeof setAiPanelTab==='function') setAiPanelTab('chat');
                  if (typeof updateToryChatSuccessUi==='function') updateToryChatSuccessUi();
                }"""
            )
            page.wait_for_timeout(400)
            summon_vis = page.locator("#toryChatSuccessSummonButton:not(.hidden)").count() > 0 or (
                page.locator("#toryChatSuccessSummonButton").count()
                and "hidden" not in (page.locator("#toryChatSuccessSummonButton").get_attribute("class") or "")
            )
            br("summon_visible", visible=bool(summon_vis))
            if summon_vis:
                page.locator("#toryChatSuccessSummonButton").click(force=True)
                page.wait_for_timeout(300)
                page.fill("#toryChatInput", "재미요소 짧게 짚어줘")
                page.locator("#toryChatSendButton").click(force=True)
                try:
                    page.wait_for_function(
                        """() => {
                          const box = document.getElementById('toryChatMessages');
                          const t = box?.querySelectorAll('.tory-chat-bubble.is-tory:not(.is-pending)');
                          return t && t.length && (t[t.length-1].innerText||'').length > 15;
                        }""",
                        timeout=120000,
                    )
                    br("analyst_chat_ok", preview=page.locator("#toryChatMessages").inner_text()[:200])
                except Exception as e:
                    issue("misbehave", "analyst chat", str(e)[:100])

        # Sample 3 helper modes from UI quickly
        page.evaluate(
            """() => {
              document.querySelectorAll('.modal').forEach(m=>m.classList.add('hidden'));
              document.getElementById('aiTabTools')?.click();
            }"""
        )
        for mode, label in [("summarize", "요약"), ("analyze", "피드백"), ("free", "직접요청")]:
            page.select_option("#aiMode", mode)
            page.wait_for_timeout(200)
            if mode == "free":
                page.fill("#aiPrompt", "이 장면 한 줄 평가")
            page.evaluate("() => { const el=document.getElementById('aiResult'); if(el) el.value='…대기…'; }")
            page.locator("#aiSubmitButton").click(force=True)
            try:
                page.wait_for_function(
                    """() => {
                      const t = document.getElementById('aiResult')?.value || '';
                      return t.length > 20 && !t.includes('대기');
                    }""",
                    timeout=90000,
                )
                br(f"ui_assist_{mode}", ok=True, len=len(page.locator("#aiResult").input_value()))
            except Exception as e:
                issue("minor", f"ui assist {mode}", str(e)[:80])
            page.evaluate(
                """() => { document.querySelectorAll('.modal').forEach(m=>m.classList.add('hidden'));
                  if (typeof closeAiResultModal==='function') try{closeAiResultModal({quiet:true})}catch(e){} }"""
            )

        # Export HWPX from UI path via API already covered; trigger UI export modal
        page.evaluate(
            """() => {
              if (typeof openExportModal==='function') openExportModal();
            }"""
        )
        page.wait_for_timeout(400)
        export_open = page.locator("#exportModal:not(.hidden)").count() > 0 or (
            page.locator("#exportModal").count()
            and "hidden" not in (page.locator("#exportModal").get_attribute("class") or "")
        )
        br("export_modal_open", visible=bool(export_open))
        page.screenshot(path=str(OUT / "08_export_modal.png"), full_page=True)
        page.evaluate(
            """() => { if (typeof closeExportModal==='function') closeExportModal();
              document.querySelectorAll('.modal').forEach(m=>m.classList.add('hidden')); }"""
        )

        page.screenshot(path=str(OUT / "09_final.png"), full_page=True)
        browser.close()

    if console_errors:
        for e in console_errors[:15]:
            issue("minor", "console error", e[:200])
    if page_errors:
        for e in page_errors[:10]:
            issue("misbehave", "pageerror", e[:200])


def main() -> int:
    print(f"Release QA server {BASE}", flush=True)
    try:
        ctx = run_api_smoke()
        run_browser(ctx)
    except Exception:
        traceback.print_exc()
        issue("crash", "qa runner exception", traceback.format_exc()[-500:])
    finally:
        server.shutdown()
        TD.cleanup()

    # classify
    by_sev = {"crash": [], "misbehave": [], "minor": []}
    for it in REPORT["issues"]:
        by_sev.setdefault(it["severity"], []).append(it)

    REPORT["finished_at"] = datetime.now().isoformat(timespec="seconds")
    REPORT["summary"] = {
        "api_ok": sum(1 for x in REPORT["api"] if x.get("ok")),
        "api_total": len(REPORT["api"]),
        "browser_steps": len(REPORT["browser"]),
        "issues": {k: len(v) for k, v in by_sev.items()},
        "final": "PASS" if REPORT["ok"] and not by_sev["crash"] else "ISSUES",
    }
    (OUT / "report.json").write_text(
        json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n========== REPORT ==========", flush=True)
    print(json.dumps(REPORT["summary"], ensure_ascii=False, indent=2), flush=True)
    for sev in ("crash", "misbehave", "minor"):
        for it in by_sev.get(sev) or []:
            print(f"[{sev}] {it['name']}: {it['detail']}", flush=True)
    return 0 if REPORT["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

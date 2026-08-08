"""
End-to-end browser flow for 흥행 공식 기능.
Uses Playwright Chromium against a freshly started SuperTory server.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Isolate DB so we don't touch the user's real works
TD = tempfile.TemporaryDirectory()
os.environ.setdefault("SUPERTORY_E2E", "1")

import app  # noqa: E402

app.DATA_DIR = Path(TD.name) / "data"
app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
app.initialise_database()

# Start server on free port
server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
port = int(server.server_port)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"
print(f"[e2e] server {BASE}", flush=True)

OUT_DIR = ROOT / "build" / "e2e_success_formula"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Virtual 구작 — front 10 episodes
def make_front_txt() -> Path:
    parts = []
    for i in range(1, 11):
        body = (
            f"제{i}화 본문. 주인공 서연이 사건을 마주한다. "
            f"대화가 빠르게 오가고, 회차 말미에 궁금증 훅이 남는다. "
            f"「이게 끝일까?」 서연이 중얼거렸다. "
        ) * 25
        parts.append(f"# {i}화 서연의 밤\n\n{body}\n")
    path = OUT_DIR / "legacy_front_10.txt"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def main() -> int:
    from playwright.sync_api import sync_playwright, expect

    front_file = make_front_txt()
    report: dict = {"base": BASE, "steps": [], "ok": True, "issues": []}

    def step(name: str, **extra):
        entry = {"name": name, **extra}
        report["steps"].append(entry)
        print(f"[e2e] {name}: {json.dumps({k: v for k, v in extra.items() if k != 'text'}, ensure_ascii=False)[:200]}", flush=True)
        return entry

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        page.on("console", lambda msg: print(f"[console.{msg.type}] {msg.text}", flush=True))
        page.on("pageerror", lambda err: report["issues"].append(f"pageerror: {err}"))

        # Skip first-run onboarding (driver.js overlay blocks clicks)
        page.add_init_script(
            """() => {
              try {
                localStorage.setItem('tutorial_completed', '1');
                localStorage.setItem('supertory.tutorial_completed', '1');
                localStorage.setItem('supertory.welcomePlusGuideDismissed', '1');
                localStorage.setItem('supertory.onboardingDone', '1');
              } catch (e) {}
            }"""
        )
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(200)
        page.evaluate("() => { try { localStorage.setItem('tutorial_completed', '1'); } catch(e) {} }")
        # Wait past maybeStartOnboardingTutorial(700ms) then kill overlay forever
        page.wait_for_timeout(900)

        def kill_tutorial():
            page.evaluate(
                """() => {
                  try { localStorage.setItem('tutorial_completed', '1'); } catch(e) {}
                  try {
                    if (window.onboardingDriver) {
                      window.onboardingDriver.destroy();
                      window.onboardingDriver = null;
                    }
                  } catch(e) {}
                  document.querySelectorAll(
                    '.driver-overlay, .driver-popover, .driver-active-element, .driver-stage, [class*="driver-"]'
                  ).forEach((el) => el.remove());
                  document.body.classList.remove('driver-active', 'driver-fade');
                  document.documentElement.classList.remove('driver-active');
                }"""
            )

        for _ in range(8):
            kill_tutorial()
            if page.locator(".driver-overlay").count() == 0:
                break
            page.wait_for_timeout(150)
        # Pointer-events safety net
        page.add_style_tag(content=".driver-overlay, .driver-popover { display:none !important; pointer-events:none !important; }")
        page.screenshot(path=str(OUT_DIR / "00_welcome.png"), full_page=True)

        # Create project via API (reliable), then load UI
        created = page.evaluate(
            """async () => {
              const r = await fetch('/api/projects', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({title:'E2E 신작 흥행테스트', main_genre:'판타지', purpose:'general_novel'})
              });
              const data = await r.json();
              return {status: r.status, data};
            }"""
        )
        if created.get("status") not in (200, 201):
            report["issues"].append(f"project create failed: {created}")
            report["ok"] = False
        # Reload projects in UI
        page.evaluate(
            """async () => {
              if (typeof loadProjects === 'function') await loadProjects();
            }"""
        )
        page.wait_for_timeout(500)
        projects = page.evaluate("""async () => (await (await fetch('/api/projects')).json())""")
        pid = (created.get("data") or {}).get("id") or (projects[0]["id"] if projects else None)
        step("project_ready", project_id=pid, title=(created.get("data") or {}).get("title"))

        # Select project
        page.select_option("#projectSelect", str(pid))
        page.wait_for_timeout(1000)

        # Create chapter + scene via API (reliable), then open
        seed = page.evaluate(
            """async (pid) => {
              const ch = await (await fetch(`/api/projects/${pid}/chapters`, {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({title:'1장'})
              })).json();
              const sc = await (await fetch(`/api/chapters/${ch.id}/scenes`, {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({title:'1화 신작 원고'})
              })).json();
              const d = await (await fetch(`/api/scenes/${sc.id}`)).json();
              const content = '<p>주인공이 마을에 도착했다. 풍경 묘사가 길고 회차 끝은 잔잔하다. 대사는 거의 없다. 긴장감이 약하다.</p>'.repeat(8);
              await fetch(`/api/scenes/${sc.id}`, {
                method:'PUT', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({title:'1화 신작 원고', status:'draft', content_md: content, row_version: d.row_version})
              });
              return {chapterId: ch.id, sceneId: sc.id};
            }""",
            pid,
        )
        page.reload(wait_until="networkidle")
        page.select_option("#projectSelect", str(pid))
        page.wait_for_timeout(1000)
        # Click scene in outline
        kill_tutorial()
        # Open scene via app function (overlay-safe)
        opened = page.evaluate(
            f"""async () => {{
              try {{
                if (typeof openScene === 'function') {{
                  await openScene({seed['sceneId']});
                  return 'openScene';
                }}
              }} catch (e) {{ return 'err:' + e; }}
              return 'no-openScene';
            }}"""
        )
        step("open_scene_call", result=opened)
        page.wait_for_timeout(600)
        # force-click if needed
        if page.locator("#sceneWorkspace.hidden, #sceneWorkspace[hidden]").count():
            page.locator(f'[data-scene="{seed["sceneId"]}"]').first.click(force=True)
            page.wait_for_timeout(500)
        page.screenshot(path=str(OUT_DIR / "01_scene_open.png"), full_page=True)
        step("scene_open", **seed)

        # Open AI panel / tools
        if page.locator("#aiPanelToggle, #openAiPanelButton, [data-open-ai]").count():
            page.locator("#aiPanelToggle, #openAiPanelButton").first.click()
        # Try common openers
        for sel in ["#aiPanel", ".ai-panel", "#toryPanel"]:
            if page.locator(sel).count():
                break
        # Force open via JS if available
        page.evaluate("""() => {
          if (typeof setAiPanelOpen === 'function') setAiPanelOpen(true);
          const tab = document.getElementById('aiTabTools');
          if (tab) tab.click();
        }""")
        page.wait_for_timeout(500)

        # Select 흥행 공식 분석
        page.select_option("#aiMode", "successpattern")
        page.wait_for_timeout(400)
        wizard = page.locator("#successPatternWizard")
        expect(wizard).not_to_have_class("hidden", timeout=5000)
        page.screenshot(path=str(OUT_DIR / "02_wizard_step1.png"), full_page=True)
        step("wizard_visible")

        # Step 1 → next
        page.locator("#spNextButton").click()
        page.wait_for_timeout(200)
        page.fill("#spWorkTitle", "가상 흥행 구작")
        page.locator("#spNextButton").click()
        page.wait_for_timeout(200)
        page.fill("#spTotalChapters", "300")
        page.locator("#spNextButton").click()
        page.wait_for_timeout(200)
        # Step 4: only front
        page.locator('[data-sp-section="front"]').check()
        page.locator('[data-sp-section="middle"]').uncheck()
        page.locator('[data-sp-section="ending"]').uncheck()
        page.locator("#spNextButton").click()
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT_DIR / "03_wizard_step5_ranges.png"), full_page=True)
        ep_summary = page.locator("#spEpisodeBudgetSummary").inner_text()
        step("episode_budget", summary=ep_summary)
        page.locator("#spNextButton").click()
        page.wait_for_timeout(400)

        # Step 6 upload
        page.locator('[data-sp-upload-btn="front"]').click()
        page.wait_for_timeout(200)
        page.set_input_files("#spFileInput", str(front_file))
        page.wait_for_timeout(2000)
        status_text = page.locator('[data-sp-upload-status="front"]').inner_text()
        step("upload_front", status=status_text)
        page.screenshot(path=str(OUT_DIR / "04_wizard_upload.png"), full_page=True)

        # Analyze
        analyze = page.locator("#spAnalyzeButton")
        expect(analyze).to_be_enabled(timeout=10000)
        analyze.click()
        # Wait for result
        page.wait_for_function(
            """() => {
              const el = document.getElementById('aiResult');
              const st = document.getElementById('spAnalyzeStatus');
              const t = (el && el.value) || '';
              const s = (st && st.textContent) || '';
              return t.includes('흥행 공식 프로파일') || s.includes('저장 완료');
            }""",
            timeout=180000,
        )
        page.wait_for_timeout(500)
        profile_text = page.locator("#aiResult").input_value()
        status_done = page.locator("#spAnalyzeStatus").inner_text()
        page.screenshot(path=str(OUT_DIR / "05_profile_result.png"), full_page=True)
        step(
            "profile_created",
            status=status_done,
            has_reader="독자 관점" in profile_text,
            has_editor="편집자" in profile_text,
            has_must="놓치지" in profile_text or "강조" in profile_text,
            preview=profile_text[:1200],
        )
        (OUT_DIR / "05_profile_result.txt").write_text(profile_text, encoding="utf-8")

        # Check link
        link_info = page.evaluate(
            """async (pid) => {
              const o = await (await fetch(`/api/projects/${pid}/outline`)).json();
              const link = o.project && o.project.linked_success_profile_id;
              let profile = null;
              if (link) {
                profile = await (await fetch(`/api/success-pattern/profiles/${link}`)).json();
              }
              return {
                linked_success_profile_id: link,
                stateLinked: window.state && window.state.linkedSuccessProfileId,
                profileTitle: profile && profile.work_title,
                factors: profile && profile.profile,
              };
            }""",
            pid,
        )
        step("auto_link", **{k: v for k, v in link_info.items() if k != "factors"})
        if not link_info.get("linked_success_profile_id"):
            report["issues"].append("프로파일이 신작에 자동 연결되지 않음")
            report["ok"] = False
        factors = link_info.get("factors") or {}
        (OUT_DIR / "05_profile_json.json").write_text(
            json.dumps(factors, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Settings codex: check if UI shows link (may not exist)
        page.evaluate("""() => {
          if (typeof setActiveBinder === 'function') setActiveBinder('settings');
          const tab = document.getElementById('binderTabSettings');
          if (tab) tab.click();
        }""")
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT_DIR / "06_settings_binder.png"), full_page=True)
        settings_html = page.locator("#settingsAccordion").inner_html()
        has_link_ui = (
            "linked_success" in settings_html
            or "흥행 프로파일" in settings_html
            or ("흥행 공식" in settings_html and "연결" in settings_html)
        )
        step("settings_link_ui", visible_in_settings=has_link_ui)
        if not has_link_ui:
            report["issues"].append(
                "설정집 UI에 프로파일 연결 상태 표시가 없음 (API에는 연결됨, 전용 표시 UI 미구현)"
            )
        # Sync client state after auto-link (ensure checkbox/summon see linked id)
        page.evaluate(
            """async (pid) => {
              const o = await (await fetch(`/api/projects/${pid}/outline`)).json();
              const link = o.project && o.project.linked_success_profile_id;
              if (typeof state !== 'undefined') {
                state.linkedSuccessProfileId = link || null;
                state.linkedSuccessProfile = null;
              }
              if (typeof ensureLinkedSuccessProfile === 'function' && link) {
                await ensureLinkedSuccessProfile();
              }
              if (typeof updateSuccessProfileRefUi === 'function') updateSuccessProfileRefUi();
              if (typeof updateToryChatSuccessUi === 'function') updateToryChatSuccessUi();
            }""",
            pid,
        )

        # Ensure scene open again
        page.evaluate(
            f"""async () => {{
              if (typeof openScene === 'function') await openScene({seed['sceneId']});
              if (typeof setAiPanelOpen === 'function') setAiPanelOpen(true);
              const tab = document.getElementById('aiTabTools');
              if (tab) tab.click();
            }}"""
        )
        page.wait_for_timeout(600)

        # 흥행 공식 피드백
        page.select_option("#aiMode", "successfeedback")
        page.wait_for_timeout(300)
        page.locator("#aiSubmitButton").click()
        page.wait_for_function(
            """() => {
              const el = document.getElementById('aiResult');
              const t = el && el.value || '';
              return t.length > 40 && !t.includes('비교하는 중');
            }""",
            timeout=120000,
        )
        feedback = page.locator("#aiResult").input_value()
        page.screenshot(path=str(OUT_DIR / "07_success_feedback.png"), full_page=True)
        (OUT_DIR / "07_success_feedback.txt").write_text(feedback, encoding="utf-8")
        step("success_feedback", length=len(feedback), preview=feedback[:800])
        if len(feedback) < 40:
            report["issues"].append("흥행 공식 피드백 결과가 비어 있거나 너무 짧음")
            report["ok"] = False

        def close_ai_modals():
            page.evaluate(
                """() => {
                  document.querySelectorAll('.modal').forEach((m) => m.classList.add('hidden'));
                  if (typeof closeAiResultModal === 'function') {
                    try { closeAiResultModal({ quiet: true }); } catch (e) {}
                  }
                }"""
            )
            page.wait_for_timeout(200)

        close_ai_modals()

        # Brainstorm + 흥행 공식 참고 checkbox
        page.select_option("#aiMode", "brainstorm")
        page.wait_for_timeout(400)
        page.evaluate(
            """() => {
              if (typeof updateSuccessProfileRefUi === 'function') updateSuccessProfileRefUi();
            }"""
        )
        chk_wrap = page.locator("#successProfileRefWrap")
        chk_visible = chk_wrap.count() and not ("hidden" in (chk_wrap.get_attribute("class") or ""))
        step("checkbox_visible_after_link", visible=bool(chk_visible))
        if not chk_visible:
            report["issues"].append("프로파일 연결 후에도 흥행 공식 참고 체크박스가 안 보임")
            report["ok"] = False
        else:
            page.locator("#successProfileRefCheck").check(force=True)
        if page.locator("#brainstormTopic").count():
            page.fill("#brainstormTopic", "미니 악역 추가 아이디어")
        # Clear previous result so we don't mistake feedback text for brainstorm
        page.evaluate("() => { const el = document.getElementById('aiResult'); if (el) el.value = '…브레인스토밍 대기…'; }")
        page.locator("#aiSubmitButton").click(force=True)
        page.wait_for_function(
            """() => {
              const el = document.getElementById('aiResult');
              const t = el && el.value || '';
              return t.length > 40 && !t.includes('대기') && !t.includes('중…') && !t.includes('중...');
            }""",
            timeout=120000,
        )
        brain = page.locator("#aiResult").input_value()
        page.screenshot(path=str(OUT_DIR / "08_brainstorm_with_ref.png"), full_page=True)
        (OUT_DIR / "08_brainstorm_with_ref.txt").write_text(brain, encoding="utf-8")
        step("brainstorm_with_ref", length=len(brain), preview=brain[:800])
        close_ai_modals()

        # Chat: 흥행요인 분석가
        page.evaluate("""() => {
          document.querySelectorAll('.modal').forEach((m) => m.classList.add('hidden'));
          const tab = document.getElementById('aiTabChat');
          if (tab) tab.click();
          if (typeof setAiPanelTab === 'function') setAiPanelTab('chat');
          if (typeof updateToryChatSuccessUi === 'function') updateToryChatSuccessUi();
        }""")
        page.wait_for_timeout(500)
        summon = page.locator("#toryChatSuccessSummonButton")
        summon_visible = summon.count() and not ("hidden" in (summon.get_attribute("class") or ""))
        step("summon_button_visible", visible=bool(summon_visible))
        if not summon_visible:
            report["issues"].append("흥행요인 분석가 소환 버튼이 안 보임")
            report["ok"] = False
        else:
            summon.click()
            page.wait_for_timeout(400)
            banner = page.locator("#toryChatSuccessBanner")
            banner_ok = banner.count() and not ("hidden" in (banner.get_attribute("class") or ""))
            step("analyst_session_banner", visible=bool(banner_ok))
            page.fill("#toryChatInput", "이거랑 비교해서 재미요소가 뭐가 부족해? 흥행 요인 기준으로 짧게 짚어 줘.")
            page.locator("#toryChatSendButton").click()
            page.wait_for_function(
                """() => {
                  const box = document.getElementById('toryChatMessages');
                  if (!box) return false;
                  const tories = box.querySelectorAll('.tory-chat-bubble.is-tory:not(.is-pending)');
                  return tories.length > 0 && (tories[tories.length-1].innerText || '').length > 20;
                }""",
                timeout=120000,
            )
            page.wait_for_timeout(400)
            chat_html = page.locator("#toryChatMessages").inner_text()
            page.screenshot(path=str(OUT_DIR / "09_analyst_chat.png"), full_page=True)
            (OUT_DIR / "09_analyst_chat.txt").write_text(chat_html, encoding="utf-8")
            step("analyst_chat", preview=chat_html[:900])

            # Switch to general and ensure history not mixed
            page.locator("#toryChatSuccessExitButton").click()
            page.wait_for_timeout(300)
            general_text = page.locator("#toryChatMessages").inner_text()
            mixed = "재미요소" in general_text and "분석가 세션" not in general_text
            # After exit, messages should be general history (likely empty or different)
            general_has_analyst_q = "재미요소가 뭐가 부족해" in general_text
            step(
                "history_isolation",
                general_has_analyst_question=general_has_analyst_q,
            )
            if general_has_analyst_q:
                report["issues"].append("일반 대화 히스토리에 분석가 세션 질문이 섞여 보임")
                report["ok"] = False

        page.screenshot(path=str(OUT_DIR / "10_final.png"), full_page=True)
        browser.close()

    report["final"] = "전체 흐름 정상" if report["ok"] and not report["issues"] else "이슈 있음"
    (OUT_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    TD.cleanup()
    server.shutdown()
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        Path(OUT_DIR / "error.txt").write_text(str(e), encoding="utf-8")
        raise

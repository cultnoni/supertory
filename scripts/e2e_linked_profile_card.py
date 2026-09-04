"""Screenshot the settings 흥행 프로파일 연결 card (linked / empty / multi)."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TD = tempfile.TemporaryDirectory()
import app

app.DATA_DIR = Path(TD.name) / "data"
app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
app.initialise_database()
server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
port = int(server.server_port)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"
OUT = ROOT / "build" / "e2e_linked_profile_card"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        launch_args = {"headless": True}
        if chrome.exists():
            launch_args["executable_path"] = str(chrome)
        browser = p.chromium.launch(**launch_args)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.add_init_script(
            "() => { try { localStorage.setItem('tutorial_completed','1'); } catch(e) {} }"
        )
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        page.evaluate(
            """() => {
              document.querySelectorAll('.driver-overlay').forEach((e) => e.remove());
              try { localStorage.setItem('tutorial_completed','1'); } catch(e) {}
            }"""
        )
        page.add_style_tag(
            content=".driver-overlay,.driver-popover{display:none!important;pointer-events:none!important;}"
        )

        # Create project + two profiles + link first
        info = page.evaluate(
            """async () => {
              const proj = await (await fetch('/api/projects', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({title:'연결 카드 테스트', main_genre:'fantasy', purpose:'web_novel'})
              })).json();
              const mk = async (title) => (await (await fetch('/api/success-pattern/run', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({
                  work_title: title, total_chapters: 50, dry_run: true,
                  sections: [{ key:'front', start_ep:1, end_ep:2, episodes:[
                    {title:'1화', text: ('훅으로 끝나는 회차. ' + title + ' ').repeat(40)},
                    {title:'2화', text: ('빠른 전개. ' + title + ' ').repeat(40)},
                  ]}]
                })
              })).json()).profile;
              const a = await mk('구작 알파');
              const b = await mk('구작 베타');
              await fetch(`/api/projects/${proj.id}/settings`, {
                method:'PUT', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({ linked_success_profile_id: a.id })
              });
              return { projectId: proj.id, aId: a.id, bId: b.id, aTitle: a.work_title, bTitle: b.work_title };
            }"""
        )
        page.evaluate(
            """async (pid) => {
              if (typeof loadProjects === 'function') await loadProjects(pid);
              const sel = document.getElementById('projectSelect');
              if (sel) { sel.value = String(pid); sel.dispatchEvent(new Event('change', {bubbles:true})); }
              if (typeof loadProject === 'function') await loadProject();
            }""",
            info["projectId"],
        )
        page.wait_for_timeout(800)
        page.evaluate(
            """async () => {
              if (typeof renderLinkedSuccessProfileCard === 'function') await renderLinkedSuccessProfileCard();
            }"""
        )
        page.wait_for_timeout(400)

        # Dock widget: linked summary, direct switch, settings sync, and shortcuts.
        page.evaluate(
            """() => {
              document.body.classList.remove('driver-active', 'driver-fade');
              document.querySelectorAll('.driver-overlay,.driver-popover').forEach((e) => e.remove());
              setBinderPanelOpen(false);
            }"""
        )
        rail_button = page.locator('[data-dock-item="successProfile"]')
        rail_button.click()
        widget = page.locator('[data-float-key="dock:successProfile"]')
        widget.wait_for(state="visible")
        assert "is-open" in (rail_button.get_attribute("class") or "")
        assert widget.locator("[data-resize-edge='se']").is_visible()
        rail_button.click()
        widget.wait_for(state="detached")
        assert "is-open" not in (rail_button.get_attribute("class") or "")
        rail_button.click()
        widget = page.locator('[data-float-key="dock:successProfile"]')
        widget.wait_for(state="visible")
        page.wait_for_function(
            """(title) => document.querySelector(
              '[data-float-key="dock:successProfile"]'
            )?.textContent.includes(title)""",
            arg=info["bTitle"],
        )
        assert widget.locator('[data-role="dock-success-profile-select"]').is_visible()
        assert widget.locator('[data-role="dock-success-feedback"]').is_enabled()
        assert widget.locator('[data-role="dock-success-chat"]').is_enabled()

        widget.locator('[data-role="dock-success-profile-select"]').select_option(str(info["aId"]))
        page.wait_for_function(
            """(title) => document.querySelector(
              '[data-float-key="dock:successProfile"]'
            )?.textContent.includes(title)""",
            arg=info["aTitle"],
        )

        page.evaluate(
            """async () => {
              await linkSuccessProfileToProject(null, { quiet: true });
            }"""
        )
        page.wait_for_function(
            """() => document.querySelector(
              '[data-float-key="dock:successProfile"]'
            )?.textContent.includes('아직 연결된 흥행작 프로파일이 없어요')"""
        )
        assert widget.locator('[data-role="dock-success-feedback"]').is_disabled()
        page.evaluate(
            """async (id) => {
              await linkSuccessProfileToProject(id, { quiet: true });
            }""",
            info["bId"],
        )
        page.wait_for_function(
            """(title) => document.querySelector(
              '[data-float-key="dock:successProfile"]'
            )?.textContent.includes(title)""",
            arg=info["bTitle"],
        )
        page.screenshot(path=str(OUT / "05_success_profile_dock_summary.png"), full_page=True)

        widget.locator('[data-role="dock-success-feedback"]').click()
        assert page.locator("#aiMode").input_value() == "successfeedback"
        page.evaluate(
            """() => document.querySelector(
              '[data-float-key="dock:successProfile"] [data-role="dock-success-chat"]'
            )?.click()"""
        )
        page.wait_for_function("() => getToryChatMode() === 'successAnalysis'")
        page.evaluate(
            """() => document.querySelector(
              '[data-float-key="dock:successProfile"] [data-role="dock-success-full"]'
            )?.click()"""
        )
        page.wait_for_function("() => state.settingsCollectionKind === 'successProfile'")
        page.evaluate(
            """() => document.querySelector(
              '[data-float-key="dock:successProfile"] [data-role="dock-success-new"]'
            )?.click()"""
        )
        page.wait_for_function(
            "() => !document.querySelector('#successPatternModal')?.classList.contains('hidden')"
        )
        page.screenshot(path=str(OUT / "05_success_profile_dock.png"), full_page=True)

        # Full settings panel
        page.screenshot(path=str(OUT / "04_settings_full.png"), full_page=True)
        browser.close()

    print("OK", info)
    TD.cleanup()
    server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

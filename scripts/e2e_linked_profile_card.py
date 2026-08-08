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
        browser = p.chromium.launch(headless=True)
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
                body: JSON.stringify({title:'연결 카드 테스트', main_genre:'판타지', purpose:'general_novel'})
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
              if (typeof setActiveBinder === 'function') setActiveBinder('settings');
              if (typeof renderLinkedSuccessProfileCard === 'function') await renderLinkedSuccessProfileCard();
            }"""
        )
        page.wait_for_timeout(400)
        # Linked + multi dropdown
        page.locator("#linkedSuccessProfilePanel").scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / "01_linked_with_dropdown.png"), full_page=False)
        card = page.locator("#linkedSuccessProfilePanel")
        page.screenshot(path=str(OUT / "01_card_linked.png"), clip=None)
        # crop-ish: just card bounding box
        box = card.bounding_box()
        if box:
            page.screenshot(path=str(OUT / "01_card_linked_crop.png"), clip=box)

        status = page.locator("#linkedSuccessProfileStatus").inner_text()
        select_visible = "hidden" not in (page.locator("#linkedSuccessProfileSelectWrap").get_attribute("class") or "")
        (OUT / "01_status.txt").write_text(
            json.dumps({"status": status, "select_visible": select_visible, "info": info}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Unlink via API+UI refresh (same as button after confirm)
        page.evaluate(
            """async () => {
              if (typeof linkSuccessProfileToProject === 'function') {
                await linkSuccessProfileToProject(null, { quiet: true });
              }
              if (typeof renderLinkedSuccessProfileCard === 'function') {
                await renderLinkedSuccessProfileCard();
              }
            }"""
        )
        page.wait_for_timeout(500)
        box2 = card.bounding_box()
        if box2:
            page.screenshot(path=str(OUT / "02_card_unlinked.png"), clip=box2)
        status2 = page.locator("#linkedSuccessProfileStatus").inner_text()
        (OUT / "02_status.txt").write_text(status2, encoding="utf-8")

        # Re-link via dropdown to beta
        page.select_option("#linkedSuccessProfileSelect", str(info["bId"]))
        page.wait_for_timeout(700)
        page.evaluate("async () => { if (typeof renderLinkedSuccessProfileCard === 'function') await renderLinkedSuccessProfileCard(); }")
        page.wait_for_timeout(300)
        box3 = card.bounding_box()
        if box3:
            page.screenshot(path=str(OUT / "03_card_switched.png"), clip=box3)
        status3 = page.locator("#linkedSuccessProfileStatus").inner_text()
        (OUT / "03_status.txt").write_text(status3, encoding="utf-8")

        # Full settings panel
        page.screenshot(path=str(OUT / "04_settings_full.png"), full_page=True)
        browser.close()

    print("OK", info)
    print("status linked:", status)
    print("status unlinked:", status2)
    print("status switched:", status3)
    TD.cleanup()
    server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

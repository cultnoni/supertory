"""Open admin 정보 panel and screenshot Tory intro."""
from __future__ import annotations

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
OUT = ROOT / "build" / "e2e_tory_intro"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.add_init_script(
            "() => { try { localStorage.setItem('tutorial_completed','1'); } catch(e) {} }"
        )
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(900)
        page.evaluate(
            """() => {
              document.querySelectorAll('.driver-overlay,.driver-popover').forEach((e) => e.remove());
              try { localStorage.setItem('tutorial_completed','1'); } catch(e) {}
            }"""
        )
        page.add_style_tag(
            content=".driver-overlay,.driver-popover{display:none!important;pointer-events:none!important;}"
        )
        # Open admin → info tab
        page.evaluate(
            """() => {
              if (typeof openAdminModal === 'function') openAdminModal('info');
              else document.getElementById('adminModeButton')?.click();
            }"""
        )
        page.wait_for_timeout(400)
        page.locator('#adminModal .admin-tab[data-admin-tab="info"]').click(force=True)
        page.wait_for_timeout(300)
        intro = page.locator(".admin-tory-intro")
        intro.wait_for(state="visible", timeout=10000)
        text = intro.inner_text()
        (OUT / "intro_text.txt").write_text(text, encoding="utf-8")
        box = intro.bounding_box()
        if box:
            page.screenshot(path=str(OUT / "tory_intro_crop.png"), clip=box)
        page.screenshot(path=str(OUT / "admin_info_full.png"), full_page=False)
        browser.close()

    print(text)
    assert "창작의 즐거움" in text
    assert "그럼 함께, 수퍼 스토리 만들어볼까요?" in text
    assert "그럼 토리와 함께" not in text
    TD.cleanup()
    server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

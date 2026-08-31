"""Static FE/BE contract checks for non-AI SuperTory features."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
app_py = (ROOT / "app.py").read_text(encoding="utf-8")
css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")

PASS = 0
FAIL = 0
NOTES: list[tuple[str, str, str]] = []


def ok(name: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    print(f"  OK  {name}" + (f" — {detail}" if detail else ""))


def fail(sev: str, name: str, detail: str) -> None:
    global FAIL
    FAIL += 1
    NOTES.append((sev, name, detail))
    print(f"  FAIL[{sev}] {name} — {detail}")


def main() -> int:
    print("=== Static contracts ===")

    # Settings order
    m = re.search(r"DEFAULT_SETTINGS_ORDER\s*=\s*\[([^\]]+)\]", js)
    if m and "characters" in m.group(1) and "baits" in m.group(1):
        order = re.findall(r'"([^"]+)"', m.group(1))
        ok("DEFAULT_SETTINGS_ORDER", " → ".join(order))
        if order.index("items") == order.index("characters") + 1:
            ok("items immediately after characters")
        else:
            fail("minor", "items position", str(order))
        if "items" in order and "baits" in order and order.index("baits") == order.index("items") + 1:
            ok("baits immediately after items")
        else:
            fail("minor", "baits position", str(order))
    else:
        fail("misbehave", "DEFAULT_SETTINGS_ORDER", "missing")

    sections = re.findall(r'data-settings-section="([^"]+)"', html)
    ok("HTML settings sections", " → ".join(sections))
    expected = ["ideas", "intro", "logsyn", "keywords", "world", "characters", "items", "baits", "successProfile", "toryVault", "sources"]
    if sections == expected:
        ok("HTML section order matches DEFAULT")
    else:
        fail("minor", "HTML section order", f"got {sections}")

    # outline_summary
    for name, good in [
        ("db/022_outline_summary.sql", (ROOT / "db" / "022_outline_summary.sql").exists()),
        ("app.py outline_summary", "outline_summary" in app_py),
        ("html projectOutlineSummary", 'id="projectOutlineSummary"' in html),
        ("js state.outlineSummary", "outlineSummary" in js and "outline_summary" in js),
        ("js saveOutlineSummary", "saveOutlineSummary" in js or "outline_summary: value" in js or "outline_summary" in js),
    ]:
        ok(name) if good else fail("misbehave", name, "missing")

    # baits
    for name, good in [
        ("db/023_bait.sql", (ROOT / "db" / "023_bait.sql").exists()),
        ("API /baits", "/baits" in app_py),
        ("refreshBaitsFromServer", "refreshBaitsFromServer" in js),
        ("migrateLocalBaitsToDb", "migrateLocalBaitsToDb" in js),
        ("baitModal html", 'id="baitModal"' in html),
        ("baitNotifyModal", 'id="baitNotifyModal"' in html),
        ("newBaitButton", 'id="newBaitButton"' in html),
        ("snoozeUntil mapping", "snoozeUntil" in js and "snooze_until" in app_py),
    ]:
        ok(name) if good else fail("misbehave", name, "missing")

    # relation canvas
    for name, good in [
        ("db/075_character_relations.sql", (ROOT / "db" / "075_character_relations.sql").exists()),
        ("db/076_character_relations_label_unique.sql", (ROOT / "db" / "076_character_relations_label_unique.sql").exists()),
        ("API character-canvas", "/character-canvas" in app_py),
        ("API character-relations", "/character-relations" in app_py),
        ("html relationCanvas", 'id="relationCanvas"' in html),
        ("html relationFitButton", 'id="relationFitButton"' in html),
        ("html relationFullscreenExitButton", 'id="relationFullscreenExitButton"' in html),
        ("js openRelationCanvas", "openRelationCanvas" in js),
        ("js enterRelationCanvasFullscreen", "enterRelationCanvasFullscreen" in js),
    ]:
        ok(name) if good else fail("misbehave", name, "missing")

    # 함께보기 rename (user-facing)
    user_facing_old = []
    for i, line in enumerate(js.splitlines(), 1):
        if "다른 씬 보기" in line:
            stripped = line.strip()
            # comments only are minor
            if stripped.startswith("//") or stripped.startswith("*") or "Re-host" in stripped:
                user_facing_old.append((i, "comment", stripped[:100]))
            elif "toast(" in stripped or "textContent" in stripped or "title" in stripped or "innerHTML" in stripped:
                user_facing_old.append((i, "user-facing", stripped[:100]))
            else:
                user_facing_old.append((i, "other", stripped[:100]))
    if "다른 씬 보기" in html:
        fail("misbehave", "html still has 다른 씬 보기", "label leftover")
    else:
        ok("html no 다른 씬 보기")
    if any(k == "user-facing" for _, k, _ in user_facing_old):
        fail("minor", "js user-facing 다른 씬 보기", str(user_facing_old))
    else:
        ok("js user-facing uses 함께보기", f"comment leftovers={len(user_facing_old)}")

    # settings context menu only on toggle
    if "settings-box-toggle[data-settings-toggle]" in js:
        ok("settings ctx menu on main toggle only")
    else:
        fail("misbehave", "settings ctx selector", "not found")

    # cast save + portrait
    for name, good in [
        ("sceneCharacterSaveButton", 'id="sceneCharacterSaveButton"' in html),
        ("persistSceneCharacterLinks", "persistSceneCharacterLinks" in js),
        ("characterPortraitAddButton", 'id="characterPortraitAddButton"' in html),
        ("portrait migration 024", (ROOT / "db" / "024_character_portrait.sql").exists()),
        ("delete project admin", "deleteProjectFromAdmin" in js),
    ]:
        ok(name) if good else fail("misbehave", name, "missing")

    # 함께보기 button
    if 'id="splitViewButton"' in html and "함께보기" in html:
        ok("splitViewButton labeled 함께보기")
    else:
        fail("misbehave", "splitViewButton", "missing label")

    # UI hide admin restore
    if 'id="hiddenFeaturesList"' in html and "data-ui-feature-hide" in html:
        ok("feature hide UI present")
    else:
        fail("minor", "feature hide", "incomplete")

    # Critical $() ids that must exist
    critical_ids = [
        "projectSelect",
        "outline",
        "sceneContent",
        "sceneTitle",
        "projectOutlineSummary",
        "projectSynopsis",
        "projectLogline",
        "projectWorldbuilding",
        "baitList",
        "baitModal",
        "characterList",
        "ideaBoard",
        "keywordBoard",
        "exportModal",
        "importModal",
        "adminModal",
        "viewerModal",
        "focusWriteModal",
        "writingLogModal",
        "splitViewButton",
        "statsCounts",
        "newProjectButton",
        "importDocumentButton",
        "exportDocumentButton",
        "welcome",
        "welcomePlusGuide",
    ]
    missing = [i for i in critical_ids if f'id="{i}"' not in html]
    if missing:
        fail("crash", "missing critical HTML ids", str(missing))
    else:
        ok("critical HTML ids present", f"n={len(critical_ids)}")

    # JS $() references vs HTML (sample high-risk)
    dollar_ids = set(re.findall(r"""\$\(\s*['\"]([a-zA-Z0-9_]+)['\"]\s*\)""", js))
    missing_dollar = sorted(
        i
        for i in dollar_ids
        if f'id="{i}"' not in html
        and f"id='{i}'" not in html
        # dynamically created / optional
        and not i.startswith("split")
        and i
        not in {
            # known dynamic or optional
            "toryChatPopupRoot",
        }
    )
    # Filter known false positives that are created in JS
    still = []
    for i in missing_dollar:
        if f'id="{i}"' in js or f"id='{i}'" in js or f'id=\\"{i}\\"' in js:
            continue
        # createElement patterns often set id later
        if re.search(rf"""\.id\s*=\s*['\"]{re.escape(i)}['\"]""", js):
            continue
        still.append(i)
    print(f"  INFO $() unique ids={len(dollar_ids)} missing_in_html≈{len(still)}")
    if still:
        # report first 25 as minor unless critical
        sample = still[:25]
        fail("minor", "JS $() ids missing in HTML", ", ".join(sample))
    else:
        ok("all static $() ids resolve in HTML or JS-created")

    # electron auto update
    main_js = ROOT / "electron" / "main.js"
    if main_js.exists():
        mtxt = main_js.read_text(encoding="utf-8")
        if "autoUpdater" in mtxt or "auto-updater" in mtxt or "checkForUpdates" in mtxt:
            ok("electron auto-update present")
        else:
            fail("minor", "auto-update", "not found in electron/main.js")

    # onboarding
    if "welcomePlusGuide" in html and ("onboarding" in js.lower() or "welcomePlusGuide" in js or "tutorial" in js.lower()):
        ok("onboarding / welcome guide present")
    else:
        fail("minor", "onboarding", "not found")

    print("\n========== SUMMARY ==========")
    print(f"PASS {PASS}  FAIL {FAIL}")
    for sev, name, detail in NOTES:
        print(f"  [{sev}] {name}: {detail}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

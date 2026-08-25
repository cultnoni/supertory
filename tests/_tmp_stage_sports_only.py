"""Stage sports-delta slices of mixed files. Temporary helper, not committed."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = Path.home() / "AppData" / "Local" / "Temp" / "supertory-sports-commit-bak"


def git_show(rel: str) -> str:
    return subprocess.check_output(["git", "show", f"HEAD:{rel}"], cwd=ROOT, encoding="utf-8")


def write_text(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8", newline="\n")


def backup(rel: str) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / rel, BACKUP_DIR / rel.replace("/", "__"))


def patch_app_js() -> None:
    text = git_show("web/app.js")
    old = (
        '  traditional: "app.정통판타지",\n'
        "};\n"
        "const GENRE_DETAIL_KEYS_BY_MAIN_SUB = {\n"
        '  "romance|modern": ["historical"],\n'
        '  "romance|romfant": ["oriental_romfant"],\n'
        '  "fantasy|male": ["alt_history", "murim", "urban", "hidden_world", "traditional"],\n'
        "};\n"
        "const GENRE_DETAIL_KEYS_BY_CLUSTER_SUB = {\n"
        '  romance: ["historical"],\n'
        '  romfant: ["oriental_romfant"],\n'
        '  male_fantasy: ["alt_history", "murim", "urban", "hidden_world", "traditional"],\n'
        "};"
    )
    new = (
        '  traditional: "app.정통판타지",\n'
        '  sports: "app.스포츠물",\n'
        "};\n"
        "const GENRE_DETAIL_KEYS_BY_MAIN_SUB = {\n"
        '  "romance|modern": ["historical"],\n'
        '  "romance|romfant": ["oriental_romfant"],\n'
        '  "fantasy|male": ["alt_history", "murim", "urban", "hidden_world", "traditional", "sports"],\n'
        "};\n"
        "const GENRE_DETAIL_KEYS_BY_CLUSTER_SUB = {\n"
        '  romance: ["historical"],\n'
        '  romfant: ["oriental_romfant"],\n'
        '  male_fantasy: ["alt_history", "murim", "urban", "hidden_world", "traditional", "sports"],\n'
        "};"
    )
    if old not in text:
        raise SystemExit("app.js HEAD pattern not found")
    write_text("web/app.js", text.replace(old, new, 1))


def patch_locale(rel: str, old: str, new: str) -> None:
    text = git_show(rel)
    if old not in text:
        raise SystemExit(f"locale pattern not found: {rel}")
    write_text(rel, text.replace(old, new, 1))


def main() -> None:
    mixed = [
        "web/app.js",
        "web/locales/ko.json",
        "web/locales/en.json",
        "web/locales/es.json",
    ]
    for rel in mixed:
        backup(rel)
    patch_app_js()
    patch_locale(
        "web/locales/ko.json",
        '  "app.정통판타지": "정통판타지",\n  "app.현대판타지": "현대판타지",\n  "app.신무협": "신무협",\n',
        '  "app.정통판타지": "정통판타지",\n  "app.현대판타지": "현대판타지",\n  "app.스포츠물": "스포츠물",\n  "app.신무협": "신무협",\n',
    )
    patch_locale(
        "web/locales/en.json",
        '  "app.정통판타지": "Traditional Fantasy",\n  "app.현대판타지": "Modern Fantasy",\n  "app.신무협": "Neo Wuxia",\n',
        '  "app.정통판타지": "Traditional Fantasy",\n  "app.현대판타지": "Modern Fantasy",\n  "app.스포츠물": "Sports",\n  "app.신무협": "Neo Wuxia",\n',
    )
    patch_locale(
        "web/locales/es.json",
        '  "app.정통판타지": "Fantasía tradicional",\n  "app.현대판타지": "Fantasía moderna",\n  "app.신무협": "Neo wuxia",\n',
        '  "app.정통판타지": "Fantasía tradicional",\n  "app.현대판타지": "Fantasía moderna",\n  "app.스포츠물": "Deporte",\n  "app.신무협": "Neo wuxia",\n',
    )
    print("ok", BACKUP_DIR)


if __name__ == "__main__":
    main()

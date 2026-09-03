"""Write web/build_info.json from the current git commit + package version.

Used by installer builds so the admin Info screen can show the exact commit
that was packaged (instead of a stale hardcoded fallback).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "build_info.json"
PACKAGE_JSON = ROOT / "package.json"


def _git(*args: str) -> str:
    try:
        out = subprocess.check_output(
            ["git", *args],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return (out or "").strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return ""


def package_version() -> str:
    try:
        data = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
        return str(data.get("version") or "").strip() or "0.0.0"
    except (OSError, json.JSONDecodeError, TypeError):
        return "0.0.0"


def main() -> int:
    commit = _git("rev-parse", "--short=7", "HEAD")
    full = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    # Local timezone when available; otherwise UTC with Z.
    now = datetime.now().astimezone()
    if now.tzinfo is None:
        now = datetime.now(timezone.utc)
    payload = {
        "version": package_version(),
        "commit": commit or None,
        "commit_full": full or None,
        "dirty": dirty,
        "built_at": now.isoformat(timespec="seconds"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if commit:
        print(f"Wrote {OUT.relative_to(ROOT)} — commit {commit} (dirty={dirty})")
    else:
        print(f"Wrote {OUT.relative_to(ROOT)} — no git commit available", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

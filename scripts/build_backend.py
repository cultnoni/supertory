"""Build a standalone SuperTory Python backend with PyInstaller.

Output:
  backend-dist/supertory-server/supertory-server.exe  (+ _internal/)

Also embeds GEMINI_MODEL and the Supabase pairing keys from the project .env
into ``bundled_env.py`` so the frozen server works without a user-supplied
.env. Paid AI keys (SECRET_AI_KEYS) stop the build unless
SUPERTORY_ALLOW_BUNDLED_AI_KEYS=1 is set for a local-only test build.

Usage (from repo root):
  python scripts/build_backend.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "backend-dist"
WORK = ROOT / "build" / "pyinstaller"
SPEC = ROOT / "supertory-server.spec"
SERVER_NAME = "supertory-server"
BUNDLED_ENV_PY = ROOT / "bundled_env.py"

# Keys copied from .env into the frozen fallback module.
BUNDLE_KEYS = (
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "ANTHROPIC_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "PEXELS_API_KEY",
)
SUPABASE_KEYS = (
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
)
# Paid-API secrets. Anyone with the installer can pull these out, so the build
# refuses to embed them unless this flag is set on purpose (the Supabase anon
# key is public by design and is not in this list).
SECRET_AI_KEYS = (
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "PEXELS_API_KEY",
)
ALLOW_AI_KEYS_FLAG = "SUPERTORY_ALLOW_BUNDLED_AI_KEYS"


class BundledSecretsBlocked(RuntimeError):
    """The build would ship paid AI keys inside the installer."""


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Installing PyInstaller…")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyinstaller>=6.0"],
            cwd=ROOT,
        )


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def write_bundled_env() -> dict[str, str]:
    """Generate bundled_env.py from project .env for freeze-time defaults."""
    env_path = ROOT / ".env"
    parsed = _parse_env_file(env_path)
    selected: dict[str, str] = {}
    for key in BUNDLE_KEYS:
        value = (parsed.get(key) or os.environ.get(key) or "").strip()
        if value and value not in {
            "your_gemini_api_key_here",
            "your_anthropic_api_key_here",
            "changeme",
        }:
            selected[key] = value

    blocked = [key for key in SECRET_AI_KEYS if key in selected]
    if blocked and os.environ.get(ALLOW_AI_KEYS_FLAG, "").strip() != "1":
        raise BundledSecretsBlocked(
            "빌드를 멈췄습니다: .env의 AI 키("
            + ", ".join(blocked)
            + ")가 설치 파일 안에 심어집니다.\n"
            "설치 파일을 받은 사람 누구나 이 키를 꺼내 쓸 수 있습니다.\n"
            "- 배포용 빌드: AI 중계 서버로 옮긴 뒤 빌드하세요.\n"
            "- 내 컴퓨터에서만 쓸 시험 빌드라면: "
            f"{ALLOW_AI_KEYS_FLAG}=1 을 설정하고 다시 빌드하세요."
        )
    if blocked:
        print(
            f"WARNING: {ALLOW_AI_KEYS_FLAG}=1 — AI keys are embedded "
            f"({', '.join(blocked)}). Do not distribute this build.",
            file=sys.stderr,
        )

    if "GEMINI_API_KEY" not in selected:
        print(
            "WARNING: GEMINI_API_KEY not found in .env — frozen AI features will "
            "need a user-supplied key.",
            file=sys.stderr,
        )
    else:
        print(
            f"Embedding GEMINI_API_KEY into bundled_env.py "
            f"(len={len(selected['GEMINI_API_KEY'])})."
        )

    if "ANTHROPIC_API_KEY" not in selected:
        print(
            "WARNING: ANTHROPIC_API_KEY not found in .env — frozen feedback "
            "pipeline will need a user-supplied key.",
            file=sys.stderr,
        )
    else:
        print(
            "Embedding ANTHROPIC_API_KEY into bundled_env.py "
            f"(len={len(selected['ANTHROPIC_API_KEY'])})."
        )

    missing_supabase = [key for key in SUPABASE_KEYS if key not in selected]
    if missing_supabase:
        print(
            "WARNING: Supabase keys missing from .env — frozen sync will stay "
            f"offline until they are supplied ({', '.join(missing_supabase)}).",
            file=sys.stderr,
        )
    else:
        print("Embedding Supabase credentials into bundled_env.py.")

    lines = [
        '"""Build-time env defaults for the frozen SuperTory backend.',
        "",
        "AUTO-GENERATED by scripts/build_backend.py — do not edit or commit.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "BUNDLED_ENV: dict[str, str] = {",
    ]
    for key, value in selected.items():
        lines.append(f"    {key!r}: {value!r},")
    lines.append("}")
    lines.append("")
    BUNDLED_ENV_PY.write_text("\n".join(lines), encoding="utf-8")
    return selected


def _clear_path(path: Path) -> None:
    """Remove a build output path; if locked, rename it aside."""
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
        return
    except OSError:
        pass
    trash = path.with_name(f"{path.name}.old_{os.getpid()}")
    if trash.exists():
        shutil.rmtree(trash, ignore_errors=True)
    try:
        path.rename(trash)
        print(f"Note: could not delete locked {path.name}; moved aside to {trash.name}")
    except OSError as exc:
        raise SystemExit(
            f"Cannot clear {path} (file in use). "
            "Close SuperTory / Explorer windows on that folder, then retry.\n"
            f"{exc}"
        ) from exc


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    try:
        write_bundled_env()
    except BundledSecretsBlocked as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    ensure_pyinstaller()

    _clear_path(DIST / SERVER_NAME)
    _clear_path(WORK)
    DIST.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(SPEC),
        "--distpath",
        str(DIST),
        "--workpath",
        str(WORK),
    ]
    print("Running:", " ".join(cmd))
    try:
        subprocess.check_call(cmd, cwd=ROOT)
    finally:
        # Left behind, it would hand real keys to source runs and tests
        # (apply_bundled_defaults fills blank env vars from it).
        BUNDLED_ENV_PY.unlink(missing_ok=True)

    exe = DIST / SERVER_NAME / f"{SERVER_NAME}.exe"
    if not exe.is_file():
        # Some layouts put the binary directly under distpath/name
        alt = DIST / f"{SERVER_NAME}.exe"
        if alt.is_file():
            print(f"Backend built (onefile?): {alt}")
            return 0 if _assert_no_env_files(alt.parent) else 1
        print(f"ERROR: expected backend binary not found: {exe}", file=sys.stderr)
        return 1

    if not _assert_no_env_files(exe.parent):
        return 1
    print(f"Backend ready: {exe}")
    return 0


def _assert_no_env_files(target_dir: Path) -> bool:
    """Fail the build if any .env file ended up in the shipped output."""
    leaked = sorted(target_dir.rglob("*.env"))  # "*" also matches the bare ".env"
    if leaked:
        for path in leaked:
            print(f"ERROR: .env file must not ship with the backend: {path}", file=sys.stderr)
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())

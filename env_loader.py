"""Minimal .env loader (stdlib only — no python-dotenv required).

Also supports build-time defaults from ``bundled_env.BUNDLED_ENV`` so frozen
backends (supertory-server.exe) keep working without a user-supplied .env.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def load_dotenv(path: Path | None = None, *, override: bool = False) -> Path | None:
    """Load KEY=VALUE pairs from a .env file into os.environ.

    Returns the path loaded, or None if the file was missing.
    """
    env_path = Path(path) if path is not None else Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return None

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
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
        if not key:
            continue
        # Strip matching single/double quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value
    return env_path


def discover_env_paths() -> list[Path]:
    """Candidate .env locations for source runs and frozen bundles."""
    candidates: list[Path] = []

    def _add(path: Path) -> None:
        resolved = path.resolve() if path.exists() else path
        if resolved not in candidates:
            candidates.append(resolved)

    try:
        module_dir = Path(__file__).resolve().parent
        _add(module_dir / ".env")
    except Exception:  # noqa: BLE001 — best-effort path discovery
        pass

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        _add(exe_dir / ".env")
        _add(exe_dir / "_internal" / ".env")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            _add(Path(meipass) / ".env")
    else:
        # Dev: also check cwd (e.g. started from another folder).
        _add(Path.cwd() / ".env")

    return candidates


def apply_bundled_defaults() -> None:
    """Fill missing env vars from build-time defaults (frozen installer).

    Values from a real process environment or .env always win; bundled
    defaults only apply when a key is absent or blank.
    """
    try:
        from bundled_env import BUNDLED_ENV  # type: ignore[import-not-found]
    except ImportError:
        return

    if not isinstance(BUNDLED_ENV, dict):
        return

    for key, value in BUNDLED_ENV.items():
        if not key or value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        current = os.environ.get(str(key))
        if current is None or str(current).strip() == "":
            os.environ[str(key)] = text


def load_all_dotenv(*, override: bool = False) -> Path | None:
    """Load the first existing .env from known locations, then bundled defaults.

    Returns the .env path that was loaded, or None if only bundled defaults apply.
    """
    loaded: Path | None = None
    for path in discover_env_paths():
        if path.is_file():
            load_dotenv(path, override=override)
            loaded = path
            break
    apply_bundled_defaults()
    return loaded


def get_env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()

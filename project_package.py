"""Scrivener-style external project files for SuperTory.

Scrivener stores each work as a package (``.scriv`` folder + ``project.scrivx``).
On Windows users open the project by double-clicking the project file.

SuperTory mirrors that with a single ``작품이름.stg`` file per work:

* Created under ``projects/`` when a work is made or imported
* Contains a small JSON manifest (uuid, title, purpose)
* Double-click launches SuperTory focused on that work (via file association)

Writing content still lives in ``data/supertory.sqlite3``; the ``.stg`` file is
the portable handle users keep on the desktop or in Explorer, like a Scrivener
project icon.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

PACKAGE_FORMAT = "supertory-project"
# Older builds wrote storyguide-project; still accept those .stg files.
LEGACY_PACKAGE_FORMATS = frozenset({"storyguide-project", "supertory-project"})
PACKAGE_VERSION = 1
PACKAGE_EXTENSION = ".stg"
PROJECTS_DIRNAME = "projects"


def projects_dir(base_dir: Path) -> Path:
    """Return the folder that holds ``*.stg`` files.

    ``base_dir`` is normally the app root.  Tests may pass a temporary directory.
    """
    path = Path(base_dir) / PROJECTS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    # Helpful note for users browsing the folder in Explorer.
    readme = path / "이 폴더 안내.txt"
    if not readme.exists():
        readme.write_text(
            "이 폴더의 .stg 파일이 각 작품입니다.\n"
            "파일을 더블클릭하면 SuperTory가 그 작품을 엽니다.\n"
            "(스크리브너의 .scriv 프로젝트 파일과 비슷한 역할입니다.)\n",
            encoding="utf-8",
        )
    return path


def safe_filename(title: str) -> str:
    name = (title or "").strip() or "무제"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name or "무제")[:80]


def new_project_uuid() -> str:
    return str(uuid.uuid4())


def read_package(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"작품 파일을 찾을 수 없습니다: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("올바른 SuperTory 작품 파일(.stg)이 아닙니다.") from error
    if not isinstance(data, dict) or data.get("format") not in LEGACY_PACKAGE_FORMATS:
        raise ValueError("올바른 SuperTory 작품 파일(.stg)이 아닙니다.")
    if not data.get("uuid"):
        raise ValueError("작품 파일에 식별자(uuid)가 없습니다.")
    return data


def package_path_for(app_root: Path, title: str, project_uuid: str, existing: str | None = None) -> Path:
    if existing:
        existing_path = Path(existing)
        if existing_path.is_file():
            try:
                if read_package(existing_path).get("uuid") == project_uuid:
                    return existing_path
            except ValueError:
                pass

    root = projects_dir(app_root)
    base = safe_filename(title)
    candidate = root / f"{base}{PACKAGE_EXTENSION}"
    if not candidate.exists():
        return candidate
    try:
        if read_package(candidate).get("uuid") == project_uuid:
            return candidate
    except ValueError:
        pass
    return root / f"{base}-{project_uuid[:8]}{PACKAGE_EXTENSION}"


def write_package(
    path: Path,
    *,
    project_uuid: str,
    title: str,
    purpose: str = "novel",
    project_id: int | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": PACKAGE_FORMAT,
        "version": PACKAGE_VERSION,
        "uuid": project_uuid,
        "title": title,
        "purpose": purpose,
        "project_id": project_id,
        "app": "SuperTory",
        "note": "이 파일을 더블클릭하면 SuperTory에서 이 작품이 열립니다.",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def create_or_update_package(
    app_root: Path,
    *,
    project_uuid: str,
    title: str,
    purpose: str = "novel",
    project_id: int | None = None,
    existing_path: str | None = None,
) -> Path:
    path = package_path_for(app_root, title, project_uuid, existing_path)
    # If the title changed, remove the old package file when it still points here.
    if existing_path:
        old = Path(existing_path)
        if old.is_file() and old.resolve() != path.resolve():
            try:
                if read_package(old).get("uuid") == project_uuid:
                    old.unlink(missing_ok=True)
            except ValueError:
                pass
    return write_package(
        path,
        project_uuid=project_uuid,
        title=title,
        purpose=purpose,
        project_id=project_id,
    )


def register_windows_file_association(app_root: Path, python_exe: str | None = None) -> bool:
    """Register .stg → SuperTory for the current Windows user (no admin)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg  # type: ignore
    except ImportError:
        return False

    app_py = str((app_root / "app.py").resolve())
    if python_exe:
        python = str(Path(python_exe).resolve())
    else:
        python = sys.executable
    # Keep the console launcher so the SuperTory window stays visible and closable.
    launcher = python
    command = f'"{launcher}" "{app_py}" "%1"'
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.stg") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "SuperTory.Project")
            winreg.SetValueEx(key, "Content Type", 0, winreg.REG_SZ, "application/x-supertory-project")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\SuperTory.Project") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "SuperTory 작품")
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, r"Software\Classes\SuperTory.Project\DefaultIcon"
        ) as key:
            # Use the python executable icon; good enough without a custom .ico.
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"{launcher},0")
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, r"Software\Classes\SuperTory.Project\shell\open\command"
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        # Tell Explorer associations may have changed.
        try:
            import ctypes

            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)  # SHCNE_ASSOCCHANGED
        except Exception:
            pass
        return True
    except OSError:
        return False

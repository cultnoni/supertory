"""JSON-file persistence for typeset presets.

The current preset store is global rather than project-scoped. ``project_id`` is
kept in the repository contract so a future server-backed implementation can
scope the same service operations without changing the HTTP or service layers.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from threading import Lock

import typeset_export


_WRITE_LOCK = Lock()


class TypesetRepository:
    """Persist typeset presets in the existing seed/runtime JSON files."""

    def __init__(self, *, root: Path, data_dir: Path) -> None:
        self.root = Path(root)
        self.data_dir = Path(data_dir)

    def list_presets(self, project_id: int | None) -> list[dict]:
        del project_id  # The legacy JSON store is global.
        return [
            {"platform_id": platform_id, **deepcopy(preset)}
            for platform_id, preset in self._load_preset_map().items()
        ]

    def get_preset(self, project_id: int | None, platform_id: str) -> dict | None:
        del project_id
        key = str(platform_id or "").strip()
        preset = self._load_preset_map().get(key)
        return deepcopy(preset) if isinstance(preset, dict) else None

    def create_preset(
        self,
        project_id: int | None,
        label: str,
        values: dict,
    ) -> dict:
        del project_id
        raw = dict(values or {})
        platform_id = str(raw.pop("platform_id", "") or "").strip()
        if not platform_id:
            raise ValueError("조판양식 ID가 필요합니다.")
        presets = self._load_preset_map()
        if platform_id in presets:
            raise ValueError("이미 있는 조판양식이에요.")
        raw["label"] = str(label or "").strip()
        presets[platform_id] = typeset_export.normalize_preset(
            raw,
            platform_id=platform_id,
        )
        saved = self._save_preset_map(presets)
        return deepcopy(saved[platform_id])

    def update_preset(
        self,
        project_id: int | None,
        platform_id: str,
        values: dict,
    ) -> dict:
        del project_id
        key = str(platform_id or "").strip()
        presets = self._load_preset_map()
        if key not in presets:
            raise KeyError(key)
        presets[key] = typeset_export.normalize_preset(
            values,
            fallback=presets[key],
            platform_id=key,
        )
        saved = self._save_preset_map(presets)
        return deepcopy(saved[key])

    def delete_preset(self, project_id: int | None, platform_id: str) -> bool:
        del project_id
        key = str(platform_id or "").strip()
        presets = self._load_preset_map()
        if key not in presets:
            return False
        del presets[key]
        self._save_preset_map(presets)
        return True

    def _seed_path(self) -> Path:
        return self.root / "data" / "typeset_presets.json"

    def _runtime_path(self) -> Path:
        return self.data_dir / "typeset_presets.json"

    @staticmethod
    def _read_json_file(path: Path) -> dict:
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _merge_platform_map(
        raw: dict,
        *,
        into: dict[str, dict] | None = None,
    ) -> dict[str, dict]:
        merged = deepcopy(into) if into else deepcopy(typeset_export.DEFAULT_PRESETS)
        for key, value in raw.items():
            platform_id = str(key or "").strip()
            if not platform_id or not isinstance(value, dict):
                continue
            fallback = merged.get(platform_id) or {
                "label": platform_id,
            }
            merged[platform_id] = typeset_export.normalize_preset(
                value,
                fallback=fallback,
                platform_id=platform_id,
            )
        return merged

    def _load_preset_map(self) -> dict[str, dict]:
        presets = deepcopy(typeset_export.DEFAULT_PRESETS)
        seed = self._seed_path()
        runtime = self._runtime_path()
        presets = self._merge_platform_map(self._read_json_file(seed), into=presets)
        if runtime.resolve() != seed.resolve():
            presets = self._merge_platform_map(
                self._read_json_file(runtime),
                into=presets,
            )
        return presets

    def _save_preset_map(self, presets: dict[str, dict]) -> dict[str, dict]:
        cleaned: dict[str, dict] = {}
        for platform_id, preset in presets.items():
            key = str(platform_id or "").strip()
            if not key:
                continue
            cleaned[key] = typeset_export.normalize_preset(
                preset,
                fallback=typeset_export.DEFAULT_PRESETS.get(key),
                platform_id=key,
            )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n"
        with _WRITE_LOCK:
            self._runtime_path().write_text(payload, encoding="utf-8")
        return cleaned

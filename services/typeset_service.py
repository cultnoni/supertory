"""Business rules for typeset presets."""

from __future__ import annotations

import re
from typing import Protocol

import typeset_export


class TypesetPresetRepository(Protocol):
    def list_presets(self, project_id: int | None) -> list[dict]: ...

    def get_preset(
        self,
        project_id: int | None,
        platform_id: str,
    ) -> dict | None: ...

    def create_preset(
        self,
        project_id: int | None,
        label: str,
        values: dict,
    ) -> dict: ...

    def update_preset(
        self,
        project_id: int | None,
        platform_id: str,
        values: dict,
    ) -> dict: ...

    def delete_preset(
        self,
        project_id: int | None,
        platform_id: str,
    ) -> bool: ...


_EDITABLE_FIELDS = frozenset({
    "label",
    "font_family",
    "font_size_pt",
    "line_height_percent",
    "letter_spacing_pt",
    "paragraph_indent_pt",
    "paragraph_spacing_pt",
    "margin_left_mm",
    "margin_right_mm",
    "margin_top_mm",
    "margin_bottom_mm",
    "mobile_viewport_px",
    "is_verified",
})
_COPY_FIELDS = (
    "font_family",
    "font_size_pt",
    "line_height_percent",
    "letter_spacing_pt",
    "paragraph_indent_pt",
    "paragraph_spacing_pt",
    "margin_left_mm",
    "margin_right_mm",
    "margin_top_mm",
    "margin_bottom_mm",
    "mobile_viewport_px",
)

_CHOSEONG = (
    "g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "",
    "j", "jj", "ch", "k", "t", "p", "h",
)
_JUNGSEONG = (
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe",
    "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i",
)
_JONGSEONG = (
    "", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p",
    "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t",
)


def _hangul_syllable_to_roman(char: str) -> str:
    code = ord(char)
    if code < 0xAC00 or code > 0xD7A3:
        return char
    syllable = code - 0xAC00
    cho = syllable // 588
    jung = (syllable % 588) // 28
    jong = syllable % 28
    return f"{_CHOSEONG[cho]}{_JUNGSEONG[jung]}{_JONGSEONG[jong]}"


def slug_from_label(label: str) -> str:
    """Build the stable custom preset ID used by the existing JSON format."""
    parts: list[str] = []
    for char in str(label or "").strip():
        code = ord(char)
        if 0xAC00 <= code <= 0xD7A3:
            parts.append(_hangul_syllable_to_roman(char))
        elif char.isalnum():
            parts.append(char.lower())
        else:
            parts.append("_")
    slug = re.sub(r"_+", "_", "".join(parts)).strip("_")
    slug = re.sub(r"[^a-z0-9_]+", "", slug)
    return (slug or "preset")[:40]


def unique_preset_id(label: str, existing_ids: set[str]) -> str:
    base = slug_from_label(label)
    candidate = base
    index = 2
    while candidate in existing_ids:
        candidate = f"{base}_{index}"
        index += 1
        if index > 999:
            raise ValueError("같은 이름의 조판양식이 너무 많아요.")
    return candidate


class TypesetService:
    """Apply typeset preset rules independently from the storage backend."""

    def __init__(self, repository: TypesetPresetRepository) -> None:
        self.repository = repository

    def list_presets(self, project_id: int | None = None) -> dict:
        return self._payload(project_id)

    def get_preset(
        self,
        platform_id: str,
        project_id: int | None = None,
    ) -> dict:
        key = self._require_platform_id(platform_id)
        preset = self.repository.get_preset(project_id, key)
        if not isinstance(preset, dict):
            raise ValueError("없는 조판양식이에요.")
        return typeset_export.normalize_preset(
            preset,
            fallback=typeset_export.DEFAULT_PRESETS.get(key),
            platform_id=key,
        )

    def create_preset(
        self,
        label: str,
        *,
        copy_from: str | None = None,
        project_id: int | None = None,
    ) -> dict:
        name = str(label or "").strip()
        if not name:
            raise ValueError("조판양식 이름을 입력해 주세요.")
        name = name[:40]
        presets = self._preset_map(project_id)
        source_id = str(copy_from or "").strip()
        if source_id:
            source = presets.get(source_id)
            if not isinstance(source, dict):
                raise ValueError("복사할 조판양식을 찾지 못했어요.")
        else:
            source_id = "munpia" if "munpia" in presets else (
                self._ordered_preset_ids(presets)[0] if presets else ""
            )
            source = presets.get(source_id)
        source = typeset_export.normalize_preset(
            source or typeset_export.DEFAULT_PRESETS["munpia"],
            fallback=(
                typeset_export.DEFAULT_PRESETS.get(source_id)
                or typeset_export.DEFAULT_PRESETS["munpia"]
            ),
            platform_id=source_id,
        )
        platform_id = unique_preset_id(name, set(presets))
        values = {key: source[key] for key in _COPY_FIELDS if key in source}
        values.update({
            "platform_id": platform_id,
            "is_verified": False,
            "is_default": False,
        })
        created = self.repository.create_preset(project_id, name, values)
        payload = self._payload(project_id)
        payload.update({
            "platform_id": platform_id,
            "preset": payload["presets"].get(platform_id) or created,
        })
        return payload

    def update_preset(
        self,
        platform_id: str,
        values: dict,
        *,
        project_id: int | None = None,
    ) -> dict:
        key = self._require_platform_id(platform_id)
        current = self.get_preset(key, project_id)
        if not isinstance(values, dict):
            raise ValueError("수정할 값이 없어요.")
        patch = {field: values[field] for field in _EDITABLE_FIELDS if field in values}
        if not patch:
            raise ValueError("바꿀 조판 항목이 없어요.")
        if "font_family" in patch:
            family = str(patch.get("font_family") or "").strip()
            if not family:
                raise ValueError("글꼴 이름을 입력해 주세요.")
            if len(family) > 80:
                raise ValueError("글꼴 이름이 너무 길어요.")
            patch["font_family"] = family
        if "label" in patch:
            name = str(patch.get("label") or "").strip()
            if not name:
                raise ValueError("조판양식 이름을 입력해 주세요.")
            patch["label"] = name[:40]
        updated = typeset_export.normalize_preset(
            {**current, **patch},
            fallback=current,
            platform_id=key,
        )
        saved = self.repository.update_preset(project_id, key, updated)
        payload = self._payload(project_id)
        payload.update({
            "platform_id": key,
            "preset": payload["presets"].get(key) or saved,
        })
        return payload

    def delete_preset(
        self,
        platform_id: str,
        *,
        project_id: int | None = None,
    ) -> dict:
        key = self._require_platform_id(platform_id)
        preset = self.get_preset(key, project_id)
        if typeset_export.is_builtin_platform(key) or bool(preset.get("is_default")):
            raise ValueError("기본 조판양식은 삭제할 수 없습니다")
        if not self.repository.delete_preset(project_id, key):
            raise ValueError("없는 조판양식이에요.")
        payload = self._payload(project_id)
        payload["platform_id"] = key
        return payload

    @staticmethod
    def _require_platform_id(platform_id: str) -> str:
        key = str(platform_id or "").strip()
        if not key:
            raise ValueError("플랫폼을 선택해 주세요.")
        return key

    def _preset_map(self, project_id: int | None) -> dict[str, dict]:
        presets: dict[str, dict] = {}
        for row in self.repository.list_presets(project_id):
            item = dict(row or {})
            platform_id = str(item.pop("platform_id", "") or "").strip()
            if platform_id:
                presets[platform_id] = item
        return presets

    @staticmethod
    def _ordered_preset_ids(presets: dict[str, dict]) -> list[str]:
        ids = [
            platform_id
            for platform_id in typeset_export.PLATFORM_ORDER
            if platform_id in presets
        ]
        ids.extend(platform_id for platform_id in presets if platform_id not in ids)
        return ids

    def _payload(self, project_id: int | None) -> dict:
        presets = self._preset_map(project_id)
        return {
            "ok": True,
            "order": self._ordered_preset_ids(presets),
            "presets": presets,
        }

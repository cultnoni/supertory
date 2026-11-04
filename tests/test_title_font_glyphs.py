"""The welcome title uses a subset font (web/fonts/GowunBatang-Bold-title.ttf).

A new title wording in any locale must only use characters the subset has,
otherwise those characters silently fall back to another font.
"""

from __future__ import annotations

import json
import re
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "web" / "fonts" / "GowunBatang-Bold-title.ttf"
TITLE_KEY = "index.웰컴_타이틀"


def font_codepoints(path: Path) -> set[int]:
    """Read the unicode cmap (format 4 / 12) with the stdlib only."""
    data = path.read_bytes()
    num_tables = struct.unpack(">H", data[4:6])[0]
    cmap_offset = None
    for i in range(num_tables):
        tag, _checksum, offset, _length = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
        if tag == b"cmap":
            cmap_offset = offset
    if cmap_offset is None:
        raise ValueError("no cmap table")
    count = struct.unpack(">H", data[cmap_offset + 2: cmap_offset + 4])[0]
    points: set[int] = set()
    for i in range(count):
        platform, encoding, sub = struct.unpack(">HHI", data[cmap_offset + 4 + 8 * i: cmap_offset + 12 + 8 * i])
        if platform not in (0, 3):
            continue
        base = cmap_offset + sub
        fmt = struct.unpack(">H", data[base: base + 2])[0]
        if fmt == 4:
            seg_x2 = struct.unpack(">H", data[base + 6: base + 8])[0]
            ends = struct.unpack(f">{seg_x2 // 2}H", data[base + 14: base + 14 + seg_x2])
            starts = struct.unpack(f">{seg_x2 // 2}H", data[base + 16 + seg_x2: base + 16 + 2 * seg_x2])
            deltas = struct.unpack(f">{seg_x2 // 2}h", data[base + 16 + 2 * seg_x2: base + 16 + 3 * seg_x2])
            ro_pos = base + 16 + 3 * seg_x2
            range_offsets = struct.unpack(f">{seg_x2 // 2}H", data[ro_pos: ro_pos + seg_x2])
            for seg, (start, end) in enumerate(zip(starts, ends)):
                for cp in range(start, end + 1):
                    if cp == 0xFFFF:
                        continue
                    if range_offsets[seg] == 0:
                        glyph = (cp + deltas[seg]) & 0xFFFF
                    else:
                        addr = ro_pos + 2 * seg + range_offsets[seg] + 2 * (cp - start)
                        glyph = struct.unpack(">H", data[addr: addr + 2])[0]
                        if glyph:
                            glyph = (glyph + deltas[seg]) & 0xFFFF
                    if glyph:
                        points.add(cp)
        elif fmt == 12:
            groups = struct.unpack(">I", data[base + 12: base + 16])[0]
            for g in range(groups):
                start, end, start_glyph = struct.unpack(">III", data[base + 16 + 12 * g: base + 28 + 12 * g])
                points.update(range(start, end + 1))
    return points


class TitleFontGlyphTests(unittest.TestCase):
    def test_welcome_title_fits_subset_font_in_every_locale(self) -> None:
        points = font_codepoints(FONT)
        self.assertGreater(len(points), 20)
        for lang in ("ko", "en", "es"):
            locale = json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
            text = re.sub(r"<[^>]+>", "", locale.get(TITLE_KEY, ""))
            self.assertTrue(text.strip(), f"{lang}: {TITLE_KEY} 문구가 비어 있음")
            missing = sorted({ch for ch in text if not ch.isspace() and ord(ch) not in points})
            self.assertEqual(missing, [], f"{lang} 시작 화면 제목에 제목용 폰트에 없는 글자: {missing}")


if __name__ == "__main__":
    unittest.main()

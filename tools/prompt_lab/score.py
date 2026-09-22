"""Fill scoring.md then compute style_type averages.

Usage:
  python tools/prompt_lab/score.py tools/prompt_lab/results/<stamp>/scoring.md
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

SCORE_KEYS = ("issue_valid", "voice", "facts", "better")


def parse_table(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            header = cells
            continue
        if all(c.replace(":", "").replace("-", "").strip() == "" for c in cells):
            continue
        if len(cells) < len(header):
            cells = cells + [""] * (len(header) - len(cells))
        row = {header[i]: cells[i] if i < len(cells) else "" for i in range(len(header))}
        rows.append(row)
    return rows


def as_score(raw: str) -> int | None:
    text = (raw or "").strip()
    if text == "":
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    if value < 0 or value > 2:
        return None
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="첨삭 카드 채점 평균")
    parser.add_argument("scoring_md", type=Path)
    args = parser.parse_args(argv)
    path: Path = args.scoring_md
    if not path.is_file():
        print(f"파일이 없습니다: {path}", file=sys.stderr)
        return 2
    rows = parse_table(path.read_text(encoding="utf-8"))
    by_type: dict[str, list[int]] = defaultdict(list)
    by_model: dict[str, list[int]] = defaultdict(list)
    facts_zero: dict[str, int] = defaultdict(int)
    scored = 0
    incomplete = 0
    for row in rows:
        scores = [as_score(row.get(key, "")) for key in SCORE_KEYS]
        if all(v is None for v in scores):
            continue
        if any(v is None for v in scores):
            incomplete += 1
            continue
        total = int(sum(scores))  # type: ignore[arg-type]
        style = row.get("style_type") or "(없음)"
        model = row.get("model") or "(없음)"
        by_type[style].append(total)
        by_model[model].append(total)
        if scores[2] == 0:
            facts_zero[style] += 1
            facts_zero[f"model:{model}"] += 1
        scored += 1
        print(
            f"{row.get('case_id')} × {model}  run {row.get('run')}  "
            f"{style}  합계 {total}/8"
        )
    if not scored:
        print("채점된 행이 없습니다. scoring.md의 빈칸을 0~2로 채워 주세요.")
        return 1
    print("")
    print("style_type 평균 (케이스당 최대 8점)")
    for style, values in sorted(by_type.items()):
        avg = statistics.mean(values)
        print(
            f"  {style}: {avg:.2f}  (n={len(values)}, 새 정보 통제 0점 {facts_zero.get(style, 0)}건)"
        )
    print("")
    print("모델 평균")
    for model, values in sorted(by_model.items()):
        avg = statistics.mean(values)
        print(
            f"  {model}: {avg:.2f}  (n={len(values)}, 새 정보 통제 0점 {facts_zero.get(f'model:{model}', 0)}건)"
        )
    if incomplete:
        print(f"\n일부만 채워진 행 {incomplete}개는 평균에서 뺐습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

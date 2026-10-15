#!/usr/bin/env python3
"""
SuperTory i18n 문자열 추출 스크립트
====================================
web/app.js, web/index.html, electron/main.js 를 스캔해서
한글이 포함된 문자열 리터럴을 찾아 중복 제거 후
locales/ko.json 초안과 검토용 리포트(CSV)를 만듭니다.

사용법:
    python extract_i18n_strings.py --root C:\\Users\\cultn\\supertory

주의:
- 실제 소스 폴더에서 실행하세요 (backend-dist, dist, *.old_* 같은
  빌드 산출물 폴더는 --exclude 로 자동 제외됩니다).
- 이 스크립트는 "추출"만 합니다. 실제 코드에 t('key') 를 적용하는 건
  다음 단계(apply script, 아직 별도)에서 리포트를 검토한 뒤 진행하세요.
"""

import argparse
import csv
import json
import re
from pathlib import Path
from collections import OrderedDict

# ---- 설정 -------------------------------------------------------------

# 스캔 대상 (프로젝트 루트 기준 상대경로). 필요하면 자유롭게 추가하세요.
TARGET_FILES = [
    "web/app.js",
    "web/index.html",
    "electron/main.js",
]

# 이 이름이 경로에 포함되어 있으면 통째로 스킵 (빌드 산출물 / 백업)
EXCLUDE_MARKERS = [
    "backend-dist",
    "dist",
    "node_modules",
    ".old_",
]

KOREAN_RE = re.compile(r'[가-힣]')

# JS: "...", '...', `...` 안에 한글이 있는 문자열 리터럴
JS_STRING_RE = re.compile(
    r'(?P<quote>["\'`])(?P<body>(?:\\.|(?!(?P=quote)).)*)(?P=quote)'
)

# HTML: 태그 사이 텍스트 노드
HTML_TEXT_RE = re.compile(r'>([^<>{}\n]*[가-힣][^<>{}\n]*)<')

# HTML: title="...", placeholder="...", aria-label="...", alt="..." 등 속성
HTML_ATTR_RE = re.compile(
    r'\b(placeholder|title|aria-label|alt|value|data-tooltip)\s*=\s*"([^"]*[가-힣][^"]*)"'
)


def is_excluded(path: Path) -> bool:
    s = str(path)
    return any(marker in s for marker in EXCLUDE_MARKERS)


def extract_js_strings(text: str):
    """JS 파일에서 한글 포함 문자열 리터럴 추출. (라인번호, 문자열) 리스트 반환."""
    results = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        # 주석 라인은 건너뜀 (완벽하진 않지만 대부분 커버)
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            continue
        for m in JS_STRING_RE.finditer(line):
            body = m.group("body")
            if KOREAN_RE.search(body):
                # 이스케이프 정리
                clean = body.replace('\\"', '"').replace("\\'", "'").replace("\\n", " ").strip()
                if clean:
                    results.append((lineno, clean))
    return results


def extract_html_strings(text: str):
    results = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in HTML_TEXT_RE.finditer(line):
            s = m.group(1).strip()
            if s and KOREAN_RE.search(s):
                results.append((lineno, s))
        for m in HTML_ATTR_RE.finditer(line):
            s = m.group(2).strip()
            if s and KOREAN_RE.search(s):
                results.append((lineno, s))
        # HTML 안에 <script> 인라인 JS가 있을 수 있으니 JS 규칙도 같이 적용
        if "<script" in line or True:
            for lineno2, s in extract_js_strings(line):
                results.append((lineno, s))
    return results


def slugify_key(s: str, used_keys: set, prefix: str) -> str:
    """문자열 내용을 보고 대충 알아볼 수 있는 키를 생성. 완벽한 자동화는 아니고
    사람이 나중에 리포트에서 다듬을 수 있도록 '초안'을 만드는 용도."""
    base = re.sub(r'[^0-9A-Za-z가-힣]+', '_', s.strip())[:24].strip('_')
    if not base:
        base = "text"
    key = f"{prefix}.{base}"
    n = 2
    original = key
    while key in used_keys:
        key = f"{original}_{n}"
        n += 1
    used_keys.add(key)
    return key


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="SuperTory 프로젝트 루트 경로")
    ap.add_argument("--out-dir", default="i18n_extract_output", help="결과물 저장 폴더")
    args = ap.parse_args()

    root = Path(args.root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # string -> {"count": int, "locations": [(file, line)], "key": str}
    registry = OrderedDict()
    used_keys = set()

    for rel in TARGET_FILES:
        fpath = root / rel
        if not fpath.exists():
            print(f"[스킵] 파일 없음: {fpath}")
            continue
        if is_excluded(fpath):
            print(f"[스킵] 제외 대상: {fpath}")
            continue

        text = fpath.read_text(encoding="utf-8", errors="replace")
        if fpath.suffix == ".html":
            found = extract_html_strings(text)
        else:
            found = extract_js_strings(text)

        prefix = fpath.stem  # app, index, main

        for lineno, s in found:
            if s not in registry:
                key = slugify_key(s, used_keys, prefix)
                registry[s] = {"count": 0, "locations": [], "key": key}
            registry[s]["count"] += 1
            registry[s]["locations"].append(f"{rel}:{lineno}")

    # ---- 결과물 1: locales/ko.json 초안 ----
    ko_json = OrderedDict()
    for s, meta in registry.items():
        ko_json[meta["key"]] = s

    ko_path = out_dir / "ko.json"
    with open(ko_path, "w", encoding="utf-8") as f:
        json.dump(ko_json, f, ensure_ascii=False, indent=2)

    # ---- 결과물 2: locales/en.json 뼈대 (번역은 TODO) ----
    en_json = OrderedDict((k, "") for k in ko_json)
    en_path = out_dir / "en.json"
    with open(en_path, "w", encoding="utf-8") as f:
        json.dump(en_json, f, ensure_ascii=False, indent=2)

    # ---- 결과물 3: 검토용 CSV 리포트 (중복횟수 많은 순 정렬) ----
    csv_path = out_dir / "review_report.csv"
    rows = sorted(registry.items(), key=lambda kv: -kv[1]["count"])
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["key", "korean_text", "occurrence_count", "locations"])
        for s, meta in rows:
            writer.writerow([meta["key"], s, meta["count"], " | ".join(meta["locations"][:5])])

    print()
    print(f"고유 문자열: {len(registry)}개")
    print(f"총 등장 횟수: {sum(m['count'] for m in registry.values())}회")
    print()
    print(f"생성됨: {ko_path}")
    print(f"생성됨: {en_path}  (번역은 비어있음 - 이후 채워야 함)")
    print(f"생성됨: {csv_path}  (키 이름 검토용, 엑셀로 열어보세요)")
    print()
    print("다음 단계: review_report.csv 를 열어서 key 이름이 이상한 항목을 다듬고,")
    print("occurrence_count 가 높은 공통 문구(닫기/저장/취소 등)는 common.* 네임스페이스로")
    print("모아주면 이후 적용(apply) 단계가 훨씬 깔끔해집니다.")


if __name__ == "__main__":
    main()

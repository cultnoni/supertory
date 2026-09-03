"""Pre/post checks around ``npm run build:win``.

1. Warn if the working tree is dirty
2. Warn if local HEAD is behind origin/main
3. Write web/build_info.json from the current commit
4. Run backend freeze + electron-builder
5. Compare packaged web/app.js SHA-256 with the source tree

Any failed check prompts before continuing (unless SUPERTORY_BUILD_FORCE=1).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_APP_JS = ROOT / "web" / "app.js"


def _git(*args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        return proc.returncode, out or err
    except FileNotFoundError:
        return 127, "git not found"


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _prompt_continue(reason: str) -> bool:
    if os.environ.get("SUPERTORY_BUILD_FORCE", "").strip() in {"1", "true", "yes"}:
        print(f"SUPERTORY_BUILD_FORCE set — continuing despite: {reason}")
        return True
    print()
    print(f"⚠  경고: {reason}")
    try:
        answer = input("그래도 빌드를 계속할까요? [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    return answer in {"y", "yes"}


def check_clean_worktree() -> bool:
    code, out = _git("status", "--porcelain")
    if code != 0:
        return _prompt_continue(f"git status 실패 ({out or code})")
    if out:
        lines = [ln for ln in out.splitlines() if ln.strip()]
        preview = "\n".join(f"  {ln}" for ln in lines[:20])
        more = f"\n  … 외 {len(lines) - 20}개" if len(lines) > 20 else ""
        return _prompt_continue(
            "커밋되지 않은 변경사항이 있습니다.\n"
            f"{preview}{more}\n"
            "설치본에 의도한 커밋만 들어갔는지 확인하세요."
        )
    print("✓ 워킹 트리 클린")
    return True


def check_synced_with_origin() -> bool:
    _git("fetch", "origin", "main", "--quiet")
    code, local = _git("rev-parse", "HEAD")
    if code != 0 or not local:
        return _prompt_continue("로컬 HEAD 커밋을 읽지 못했습니다.")
    code, remote = _git("rev-parse", "origin/main")
    if code != 0 or not remote:
        print("· origin/main을 확인하지 못했습니다 (원격 미설정/오프라인). 건너뜁니다.")
        return True
    if local == remote:
        print(f"✓ HEAD == origin/main ({local[:7]})")
        return True
    code, behind = _git("rev-list", "--count", "HEAD..origin/main")
    code2, ahead = _git("rev-list", "--count", "origin/main..HEAD")
    behind_n = behind if code == 0 else "?"
    ahead_n = ahead if code2 == 0 else "?"
    if behind_n not in {"0", "?"} and behind_n != "0":
        return _prompt_continue(
            f"로컬이 origin/main보다 {behind_n}커밋 뒤처져 있습니다 "
            f"(local {local[:7]} / origin {remote[:7]}, ahead={ahead_n})."
        )
    if local != remote:
        print(
            f"· HEAD({local[:7]}) ≠ origin/main({remote[:7]}) "
            f"(ahead={ahead_n}, behind={behind_n}) — 계속합니다."
        )
    return True


def write_build_info() -> str:
    script = ROOT / "scripts" / "write_build_info.py"
    subprocess.check_call([sys.executable, str(script)], cwd=ROOT)
    code, short = _git("rev-parse", "--short=7", "HEAD")
    return short if code == 0 else ""


def run_build() -> None:
    # Mirror previous package.json: build:backend && electron-builder --win nsis
    subprocess.check_call(["npm", "run", "build:backend"], cwd=ROOT, shell=True)
    subprocess.check_call(
        ["npx", "electron-builder", "--win", "nsis"],
        cwd=ROOT,
        shell=True,
    )


def find_packaged_app_js() -> Path | None:
    candidates = [
        ROOT / "dist" / "win-unpacked" / "resources" / "supertory-server" / "_internal" / "web" / "app.js",
        ROOT / "backend-dist" / "supertory-server" / "_internal" / "web" / "app.js",
    ]
    for path in candidates:
        if path.is_file():
            return path
    # Fallback: search under dist/
    dist = ROOT / "dist"
    if dist.is_dir():
        hits = list(dist.rglob("web/app.js"))
        if hits:
            return hits[0]
    return None


def verify_packaged_app_js() -> bool | str:
    """Return True if match, 'warned' if mismatch but user continues, False if abort."""
    source_hash = _sha256(SOURCE_APP_JS)
    if not source_hash:
        if _prompt_continue(f"소스 파일을 읽지 못했습니다: {SOURCE_APP_JS}"):
            return "warned"
        return False
    packaged = find_packaged_app_js()
    if not packaged:
        if _prompt_continue(
            "빌드 산출물에서 web/app.js를 찾지 못했습니다 "
            "(dist/win-unpacked 또는 backend-dist)."
        ):
            return "warned"
        return False
    packed_hash = _sha256(packaged)
    rel = packaged.relative_to(ROOT)
    if packed_hash == source_hash:
        print(f"✓ 패키지 web/app.js 일치 ({rel})")
        print(f"  sha256={source_hash[:16]}…")
        return True
    if _prompt_continue(
        "패키지된 web/app.js가 현재 소스와 다릅니다.\n"
        f"  source : {source_hash}\n"
        f"  packaged ({rel}): {packed_hash}\n"
        "빌드가 오래된 산출물을 묶었을 수 있습니다."
    ):
        return "warned"
    return False


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    print("=== SuperTory Windows 빌드 사전 점검 ===")
    if not check_clean_worktree():
        print("빌드 취소.")
        return 1
    if not check_synced_with_origin():
        print("빌드 취소.")
        return 1

    commit = write_build_info()
    if commit:
        print(f"커밋 {commit} 기준으로 빌드 중...")
    else:
        print("커밋 해시를 얻지 못했습니다. 빌드 정보 없이 계속합니다.")
        if not _prompt_continue("git 커밋 해시 없이 빌드합니다."):
            print("빌드 취소.")
            return 1

    try:
        run_build()
    except subprocess.CalledProcessError as exc:
        print(f"빌드 실패 (exit {exc.returncode})", file=sys.stderr)
        return exc.returncode or 1

    print()
    print("=== 빌드 산출물 검증 ===")
    verified = verify_packaged_app_js()
    if verified is False:
        print("빌드 완료 메시지를 출력하지 않습니다 (검증 실패·취소).")
        return 2

    print()
    if verified is True:
        print(f"빌드 완료 ✓  (커밋 {commit or 'unknown'})")
    else:
        print(f"빌드 완료 (경고 있음)  (커밋 {commit or 'unknown'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Generate beta coupon codes and the SQL that registers them in Supabase.

Usage (from repo root):
  python scripts/make_coupons.py 10                  # 10 coupons, 30 days, 1 use each
  python scripts/make_coupons.py 5 --days 30 --note "beta wave 1"

Writes two files under .coupon_out/ (git-ignored):
  coupons-<stamp>.txt  plain codes - hand these to testers, keep them private
  coupons-<stamp>.sql  admin_create_coupon(...) calls - paste into the Supabase
                       SQL editor (it stores only the SHA-256 of each code)

The codes are never printed to the terminal.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".coupon_out"
# No 0/O/1/I/L: easy to read aloud and retype from a phone message.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
GROUPS = 4
GROUP_LEN = 4  # 16 chars ~ 79 bits


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LEN))
    return "-".join(raw[i : i + GROUP_LEN] for i in range(0, len(raw), GROUP_LEN))


def sql_literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def build(count: int, days: int, max_uses: int, note: str) -> tuple[list[str], str]:
    codes: list[str] = []
    while len(codes) < count:
        code = new_code()
        if code not in codes:
            codes.append(code)
    lines = [
        "-- AI relay coupons (generated). Run once in the Supabase SQL editor.",
        f"-- {count} coupons, {days} days, {max_uses} use each.",
    ]
    for code in codes:
        lines.append(
            "select public.admin_create_coupon("
            f"{sql_literal(code)}, {days}, {max_uses}, null, null, {sql_literal(note)});"
        )
    return codes, "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("count", type=int, help="how many coupons")
    parser.add_argument("--days", type=int, default=30, help="access days per coupon (default 30)")
    parser.add_argument("--max-uses", type=int, default=1, help="uses per coupon (default 1)")
    parser.add_argument("--note", default="beta", help="internal note")
    args = parser.parse_args(argv)
    if not 1 <= args.count <= 500 or args.days < 1 or args.max_uses < 1:
        print("count must be 1-500; days and max-uses must be >= 1", file=sys.stderr)
        return 2

    codes, sql = build(args.count, args.days, args.max_uses, args.note)
    OUT.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    txt = OUT / f"coupons-{stamp}.txt"
    sql_path = OUT / f"coupons-{stamp}.sql"
    txt.write_text("\n".join(codes) + "\n", encoding="utf-8")
    sql_path.write_text(sql, encoding="utf-8")
    print(f"{args.count} coupons ({args.days} days, {args.max_uses} use each)")
    print(f"codes: {txt}")
    print(f"sql:   {sql_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

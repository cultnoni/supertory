"""Static guards for the AI-relay coupon migration (no Postgres needed).

The SQL itself is exercised against a real Supabase project during the relay
round-trip test; here we only pin the security-relevant shape so a later edit
cannot quietly weaken it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SQL = (
    Path(__file__).resolve().parent.parent
    / "supabase"
    / "migrations"
    / "20261107100000_ai_coupons.sql"
).read_text(encoding="utf-8")


class CouponMigrationTests(unittest.TestCase):
    def test_every_table_has_rls(self) -> None:
        tables = re.findall(r"create table if not exists public\.(\w+)", SQL)
        self.assertEqual(
            sorted(tables), ["coupon_attempts", "coupon_redemptions", "coupons"]
        )
        for table in tables:
            self.assertIn(
                f"alter table public.{table}", SQL, f"{table} needs RLS enabled"
            )

    def test_coupon_codes_are_stored_hashed_only(self) -> None:
        self.assertIn("code_hash", SQL)
        self.assertNotRegex(SQL, r"\bcode\s+text")

    def test_coupons_table_has_no_app_policy(self) -> None:
        policies = re.findall(r"create policy (\w+) on public\.(\w+)", SQL)
        self.assertEqual([t for _, t in policies], ["coupon_redemptions"])

    def test_redeem_is_for_logged_in_users_only(self) -> None:
        self.assertIn(
            "grant execute on function public.redeem_coupon(text) to authenticated", SQL
        )
        self.assertIn(
            "revoke all on function public.redeem_coupon(text) from public, anon", SQL
        )

    def test_admin_create_is_service_role_only(self) -> None:
        self.assertRegex(
            SQL, r"revoke all on function public\.admin_create_coupon\([^)]*\) from public, anon, authenticated"
        )
        self.assertRegex(
            SQL, r"grant execute on function public\.admin_create_coupon\([^)]*\) to service_role"
        )

    def test_redeem_locks_the_coupon_row_and_rate_limits(self) -> None:
        self.assertIn("for update", SQL)
        self.assertIn("rate_limited", SQL)

    def test_security_definer_functions_pin_search_path(self) -> None:
        for body in re.findall(r"security definer\n(.*?)as \$\$", SQL, flags=re.S):
            self.assertIn("set search_path = public", body)

    def test_grants_access_with_coupon_source(self) -> None:
        self.assertIn("'coupon'", SQL)


if __name__ == "__main__":
    unittest.main()

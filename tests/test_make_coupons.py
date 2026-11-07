from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import make_coupons  # noqa: E402


class MakeCouponsTests(unittest.TestCase):
    def test_codes_are_unique_and_well_formed(self) -> None:
        codes, _ = make_coupons.build(200, 30, 1, "t")
        self.assertEqual(len(set(codes)), 200)
        for code in codes:
            self.assertRegex(code, r"^([2-9A-HJKMNP-Z]{4}-){3}[2-9A-HJKMNP-Z]{4}$")

    def test_sql_has_one_call_per_code_with_30_days_one_use(self) -> None:
        codes, sql = make_coupons.build(3, 30, 1, "it's beta")
        calls = re.findall(r"select public\.admin_create_coupon\((.*)\);", sql)
        self.assertEqual(len(calls), 3)
        for code, call in zip(codes, calls):
            self.assertIn(f"'{code}', 30, 1, null, null, 'it''s beta'", call)

    def test_codes_pass_the_sql_minimum_length(self) -> None:
        code = make_coupons.new_code().replace("-", "")
        self.assertGreaterEqual(len(code), 10)


if __name__ == "__main__":
    unittest.main()

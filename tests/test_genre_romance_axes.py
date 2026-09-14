# -*- coding: utf-8 -*-
"""Genre-literature romance axes + blend routing hooks."""

from __future__ import annotations

import unittest

import genre_clusters
import genre_tool_routing


class RomanceAxesNormalizeTests(unittest.TestCase):
    def test_structure_and_setting_keys(self) -> None:
        self.assertEqual(genre_clusters.normalize_romance_structure("EMOTIONAL"), "emotional")
        self.assertEqual(genre_clusters.normalize_romance_structure("plot_driven"), "plot_driven")
        self.assertEqual(genre_clusters.normalize_romance_structure("nope"), "")
        self.assertEqual(genre_clusters.normalize_romance_setting("PERIOD"), "period")
        self.assertEqual(genre_clusters.normalize_romance_setting("contemporary"), "contemporary")
        self.assertEqual(genre_clusters.normalize_romance_setting(""), "")

    def test_blend_defaults_and_romance_main_forces_none(self) -> None:
        self.assertEqual(genre_clusters.normalize_romance_blend(""), "none")
        self.assertEqual(genre_clusters.normalize_romance_blend("subplot"), "subplot")
        self.assertEqual(genre_clusters.normalize_romance_blend("CO_AXIS"), "co_axis")
        self.assertEqual(
            genre_clusters.normalize_romance_blend(
                "main_axis",
                main_genre="romance",
                cluster_id="genre_literature",
                purpose="genre_literature",
            ),
            "none",
        )

    def test_coerce_preserves_legacy_empty_for_existing_lit_works(self) -> None:
        fields = genre_clusters.coerce_romance_fields_for_project(
            cluster_id="genre_literature",
            purpose="genre_literature",
            main_genre="sf",
            sub_genre="space",
            romance_structure=None,
            romance_setting=None,
            romance_blend=None,
        )
        self.assertEqual(fields["romance_structure"], "")
        self.assertEqual(fields["romance_setting"], "")
        self.assertEqual(fields["romance_blend"], "none")

    def test_coerce_clears_axes_for_webnovel(self) -> None:
        fields = genre_clusters.coerce_romance_fields_for_project(
            cluster_id="webnovel",
            purpose="web_novel",
            main_genre="romance",
            sub_genre="modern",
            romance_structure="emotional",
            romance_setting="period",
            romance_blend="co_axis",
        )
        self.assertEqual(fields["romance_structure"], "")
        self.assertEqual(fields["romance_setting"], "")
        self.assertEqual(fields["romance_blend"], "none")

    def test_coerce_keeps_structure_for_co_axis_and_main_axis(self) -> None:
        for blend, structure in (
            ("co_axis", "emotional"),
            ("co_axis", "plot_driven"),
            ("main_axis", "emotional"),
            ("main_axis", "plot_driven"),
        ):
            fields = genre_clusters.coerce_romance_fields_for_project(
                cluster_id="genre_literature",
                purpose="genre_literature",
                main_genre="sf",
                sub_genre="space",
                romance_structure=structure,
                romance_setting="period",  # must be cleared for blend
                romance_blend=blend,
            )
            self.assertEqual(fields["romance_structure"], structure, blend)
            self.assertEqual(fields["romance_setting"], "", blend)
            self.assertEqual(fields["romance_blend"], blend)
            self.assertTrue(
                genre_clusters.romance_structure_applies(
                    cluster_id="genre_literature",
                    purpose="genre_literature",
                    main_genre="sf",
                    romance_blend=blend,
                ),
                blend,
            )
            self.assertFalse(
                genre_clusters.romance_setting_applies(
                    cluster_id="genre_literature",
                    purpose="genre_literature",
                    main_genre="sf",
                ),
                blend,
            )

    def test_coerce_clears_structure_for_subplot_and_none(self) -> None:
        for blend in ("subplot", "none"):
            fields = genre_clusters.coerce_romance_fields_for_project(
                cluster_id="genre_literature",
                purpose="genre_literature",
                main_genre="sf",
                sub_genre="space",
                romance_structure="emotional",
                romance_setting="period",
                romance_blend=blend,
            )
            self.assertEqual(fields["romance_structure"], "")
            self.assertEqual(fields["romance_setting"], "")
            self.assertEqual(fields["romance_blend"], blend)
            self.assertFalse(
                genre_clusters.romance_structure_applies(
                    cluster_id="genre_literature",
                    purpose="genre_literature",
                    main_genre="sf",
                    romance_blend=blend,
                ),
            )

    def test_period_support_module_only_for_period_romance(self) -> None:
        self.assertTrue(
            genre_clusters.is_period_support_module_enabled(
                cluster_id="genre_literature",
                purpose="genre_literature",
                main_genre="romance",
                romance_setting="period",
            )
        )
        self.assertFalse(
            genre_clusters.is_period_support_module_enabled(
                cluster_id="genre_literature",
                purpose="genre_literature",
                main_genre="romance",
                romance_setting="contemporary",
            )
        )
        self.assertFalse(
            genre_clusters.is_period_support_module_enabled(
                cluster_id="webnovel",
                purpose="web_novel",
                main_genre="romance",
                romance_setting="period",
            )
        )


class GenreToolRoutingHookTests(unittest.TestCase):
    def test_romance_main_uses_structure_toolset(self) -> None:
        plan = genre_tool_routing.resolve_genre_tool_routing(
            cluster_id="genre_literature",
            purpose="genre_literature",
            main_genre="romance",
            romance_structure="emotional",
            romance_setting="contemporary",
            romance_blend="subplot",
        )
        self.assertEqual(plan["romance_blend"], "none")
        self.assertEqual(plan["primary_toolset"], "genre_lit_romance_emotional")
        self.assertEqual(plan["secondary_toolsets"], [])
        self.assertTrue(plan["structure_applies"])
        self.assertTrue(plan["setting_applies"])
        self.assertFalse(plan["period_support_module"])

    def test_blend_none_main_only(self) -> None:
        plan = genre_tool_routing.resolve_genre_tool_routing(
            cluster_id="genre_literature",
            purpose="genre_literature",
            main_genre="sf",
            sub_genre="space",
            romance_blend="none",
            romance_structure="emotional",
        )
        self.assertEqual(plan["primary_toolset"], "genre_lit_sf")
        self.assertEqual(plan["secondary_toolsets"], [])
        self.assertEqual(plan["auxiliary_toolsets"], [])
        self.assertEqual(plan["romance_structure"], "")
        self.assertFalse(plan["structure_applies"])

    def test_blend_subplot_ignores_structure(self) -> None:
        plan = genre_tool_routing.resolve_genre_tool_routing(
            cluster_id="genre_literature",
            main_genre="sf",
            romance_blend="subplot",
            romance_structure="emotional",
        )
        self.assertEqual(plan["primary_toolset"], "genre_lit_sf")
        self.assertEqual(plan["secondary_toolsets"], ["genre_lit_romance"])
        self.assertEqual(plan["blend_weight"], "low")
        self.assertEqual(plan["romance_structure"], "")
        self.assertFalse(plan["structure_applies"])
        self.assertFalse(plan["setting_applies"])

    def test_blend_co_axis_and_main_axis_use_structure(self) -> None:
        emotional = genre_tool_routing.resolve_genre_tool_routing(
            cluster_id="genre_literature",
            main_genre="sf",
            romance_blend="co_axis",
            romance_structure="emotional",
        )
        self.assertEqual(emotional["primary_toolset"], "genre_lit_sf")
        self.assertEqual(emotional["secondary_toolsets"], ["genre_lit_romance_emotional"])
        self.assertEqual(emotional["romance_structure"], "emotional")
        self.assertTrue(emotional["structure_applies"])
        self.assertFalse(emotional["setting_applies"])
        self.assertEqual(emotional.get("blend_weight"), "equal")

        plot = genre_tool_routing.resolve_genre_tool_routing(
            cluster_id="genre_literature",
            main_genre="sf",
            romance_blend="co_axis",
            romance_structure="plot_driven",
        )
        self.assertEqual(plot["secondary_toolsets"], ["genre_lit_romance_plot_driven"])
        self.assertEqual(plot["romance_structure"], "plot_driven")

        main_axis = genre_tool_routing.resolve_genre_tool_routing(
            cluster_id="genre_literature",
            main_genre="sf",
            romance_blend="main_axis",
            romance_structure="plot_driven",
        )
        self.assertEqual(main_axis["primary_toolset"], "genre_lit_romance_plot_driven")
        self.assertEqual(main_axis["auxiliary_toolsets"], ["genre_lit_sf"])
        self.assertEqual(main_axis["romance_structure"], "plot_driven")
        self.assertTrue(main_axis["structure_applies"])

    def test_webnovel_routing_is_noop(self) -> None:
        plan = genre_tool_routing.resolve_genre_tool_routing(
            cluster_id="webnovel",
            purpose="web_novel",
            main_genre="romance",
            romance_blend="co_axis",
        )
        self.assertEqual(plan["primary_toolset"], "")
        self.assertEqual(plan["romance_blend"], "none")


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_common.backtest.factor_catalog import (
    factor_aliases,
    factor_catalog_prompt,
    load_factor_catalog,
    search_factor_candidates,
    search_factor_matches,
)


class FactorCatalogTests(unittest.TestCase):
    def test_loads_keyword_mappings_and_factor_entries(self) -> None:
        catalog = load_factor_catalog()

        self.assertIn("earnings_to_price", catalog.entries)
        self.assertTrue(
            any("市盈率" in item.phrase for item in catalog.keyword_mappings)
        )

    def test_prompt_contains_common_mapping_and_full_factor(self) -> None:
        prompt = factor_catalog_prompt()

        self.assertIn("市盈率 / PE / 估值", prompt)
        self.assertIn("earnings_to_price", prompt)
        self.assertIn("quality_composite", prompt)

    def test_splits_common_mapping_aliases_for_lookup(self) -> None:
        aliases = factor_aliases("earnings_to_price")

        self.assertIn("PE", aliases)
        self.assertIn("市盈率", aliases)

    def test_searches_factor_description_terms(self) -> None:
        candidates = search_factor_candidates("盈利能力比较强的质量因子")

        self.assertIn("quality_composite", candidates)

    def test_search_matches_include_parseable_terms_without_selection_count_noise(self) -> None:
        matches = search_factor_matches("质量综合最高的20只股票")

        self.assertEqual(matches[0].factor_name, "quality_composite")
        self.assertEqual(matches[0].matched_terms, ("质量综合",))
        self.assertNotIn("alpha101_20", [item.factor_name for item in matches])


if __name__ == "__main__":
    unittest.main()

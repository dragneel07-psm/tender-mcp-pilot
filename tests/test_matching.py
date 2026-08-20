"""Unit tests for the pure matching logic (Milestone 5) -- no DB, no HTTP, direct dict fixtures."""
import unittest

from tender_monitor.matching import match_tender_to_company


def notice(**overrides):
    base = {"title": "Construction of rural road", "authority": "Some Municipality", "province": "Sudurpashchim",
            "categories": [{"category": "Road", "confidence_score": 0.6}]}
    base.update(overrides)
    return base


def profile(**overrides):
    base = {"categories": [], "provinces": [], "keywords": []}
    base.update(overrides)
    return base


class NoCriteriaConfiguredTests(unittest.TestCase):
    def test_all_dimensions_unset_yields_zero_score_and_no_dimensions(self):
        result = match_tender_to_company(notice(), profile())
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["dimensions"], [])


class CategoryDimensionTests(unittest.TestCase):
    def test_matching_category_scores_at_classifier_confidence(self):
        result = match_tender_to_company(notice(), profile(categories=["Road"]))
        dimension = result["dimensions"][0]
        self.assertEqual(dimension["dimension"], "category")
        self.assertEqual(dimension["score"], 0.6)
        self.assertEqual(dimension["matched"], ["Road"])
        self.assertEqual(result["score"], 0.6)  # only active dimension -> overall == dimension score

    def test_non_matching_category_scores_zero_but_still_reported(self):
        result = match_tender_to_company(notice(), profile(categories=["Solar"]))
        dimension = result["dimensions"][0]
        self.assertEqual(dimension["score"], 0.0)
        self.assertEqual(dimension["matched"], [])
        self.assertEqual(result["score"], 0.0)

    def test_category_match_is_case_insensitive(self):
        result = match_tender_to_company(notice(), profile(categories=["road"]))
        self.assertEqual(result["dimensions"][0]["matched"], ["Road"])

    def test_best_of_several_matching_categories_is_used(self):
        n = notice(categories=[{"category": "Road", "confidence_score": 0.6}, {"category": "Other", "confidence_score": 0.5}])
        result = match_tender_to_company(n, profile(categories=["Road", "Other"]))
        self.assertEqual(result["dimensions"][0]["score"], 0.6)
        self.assertCountEqual(result["dimensions"][0]["matched"], ["Road", "Other"])


class ProvinceDimensionTests(unittest.TestCase):
    def test_matching_province_scores_one(self):
        result = match_tender_to_company(notice(), profile(provinces=["Sudurpashchim"]))
        self.assertEqual(result["dimensions"][0]["score"], 1.0)

    def test_non_matching_province_scores_zero(self):
        result = match_tender_to_company(notice(), profile(provinces=["Bagmati"]))
        self.assertEqual(result["dimensions"][0]["score"], 0.0)

    def test_missing_notice_province_scores_zero_without_crashing(self):
        result = match_tender_to_company(notice(province=None), profile(provinces=["Bagmati"]))
        self.assertEqual(result["dimensions"][0]["score"], 0.0)


class KeywordDimensionTests(unittest.TestCase):
    def test_keyword_found_in_title_scores_one(self):
        result = match_tender_to_company(notice(), profile(keywords=["road"]))
        self.assertEqual(result["dimensions"][0]["score"], 1.0)
        self.assertEqual(result["dimensions"][0]["matched"], ["road"])

    def test_keyword_found_in_authority_scores_one(self):
        result = match_tender_to_company(notice(), profile(keywords=["municipality"]))
        self.assertEqual(result["dimensions"][0]["score"], 1.0)

    def test_keyword_not_found_scores_zero(self):
        result = match_tender_to_company(notice(), profile(keywords=["cctv"]))
        self.assertEqual(result["dimensions"][0]["score"], 0.0)


class CombinedWeightingTests(unittest.TestCase):
    def test_weights_renormalize_over_active_dimensions_only(self):
        # Only category + keyword configured (weights 0.5 + 0.3 of the full 0.5/0.2/0.3 scheme);
        # both match, so the renormalized weighted average should still be 1.0, not capped at 0.8.
        result = match_tender_to_company(notice(), profile(categories=["Road"], keywords=["road"]))
        self.assertEqual(len(result["dimensions"]), 2)
        self.assertAlmostEqual(result["score"], (0.5 * 0.6 + 0.3 * 1.0) / 0.8)

    def test_all_three_dimensions_active(self):
        result = match_tender_to_company(notice(), profile(categories=["Road"], provinces=["Sudurpashchim"], keywords=["road"]))
        self.assertEqual(len(result["dimensions"]), 3)
        expected = 0.5 * 0.6 + 0.2 * 1.0 + 0.3 * 1.0
        self.assertAlmostEqual(result["score"], expected)

    def test_non_actionable_notice_terminal_statuses_are_named(self):
        from tender_monitor.matching import NON_ACTIONABLE_STATUSES
        self.assertEqual(set(NON_ACTIONABLE_STATUSES), {"cancelled", "awarded"})


if __name__ == "__main__":
    unittest.main()

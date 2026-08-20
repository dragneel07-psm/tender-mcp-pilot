import unittest

from tender_monitor import parsing


class LinkTextParserTests(unittest.TestCase):
    def test_extracts_href_and_text(self):
        html = '<a href="/a">First</a><p>ignored</p><a href="/b">Second Link</a>'
        parser = parsing.LinkTextParser()
        parser.feed(html)
        self.assertEqual(parser.links, [("/a", "First"), ("/b", "Second Link")])

    def test_joins_nested_tag_text_and_strips_outer_whitespace(self):
        html = '<a href="/x">  Road<b>construction</b>tender  </a>'
        parser = parsing.LinkTextParser()
        parser.feed(html)
        self.assertEqual(parser.links, [("/x", "Road construction tender")])

    def test_link_without_href_is_skipped(self):
        html = '<a name="anchor">No href here</a><a href="/y">Has href</a>'
        parser = parsing.LinkTextParser()
        parser.feed(html)
        self.assertEqual(parser.links, [("/y", "Has href")])

    def test_no_links_is_empty(self):
        parser = parsing.LinkTextParser()
        parser.feed("<p>Nothing to see here</p>")
        self.assertEqual(parser.links, [])


class OfficialDirectoryParserTests(unittest.TestCase):
    def test_extracts_rows_with_cells_and_links(self):
        html = """
        <table>
          <tr><th>S.N.</th><th>Province</th><th>स्थानीय तहको नाम</th><th>Website</th></tr>
          <tr><td>1</td><td>Sudurpashchim</td><td>Dhangadhi</td><td><a href="https://dhangadhimun.gov.np">Visit</a></td></tr>
        </table>
        """
        parser = parsing.OfficialDirectoryParser()
        parser.feed(html)
        self.assertEqual(len(parser.rows), 2)
        header_cells, header_links = parser.rows[0]
        self.assertEqual(header_cells[2], "स्थानीय तहको नाम")
        self.assertEqual(header_links, [])
        data_cells, data_links = parser.rows[1]
        self.assertEqual(data_cells[2], "Dhangadhi")
        self.assertEqual(data_links, ["https://dhangadhimun.gov.np"])

    def test_row_without_any_cells_is_not_recorded(self):
        parser = parsing.OfficialDirectoryParser()
        parser.feed("<table><tr></tr></table>")
        self.assertEqual(parser.rows, [])


class CleanTests(unittest.TestCase):
    def test_collapses_whitespace_and_unescapes_entities(self):
        self.assertEqual(parsing.clean("  Road \n\t construction &amp; bridge  "), "Road construction & bridge")

    def test_empty_string_stays_empty(self):
        self.assertEqual(parsing.clean(""), "")


class FirstDateTests(unittest.TestCase):
    def test_matches_iso_date(self):
        self.assertEqual(parsing.first_date("Published on 2026-08-15 for tender"), "2026-08-15")

    def test_matches_slash_date(self):
        self.assertEqual(parsing.first_date("date: 15/08/2026"), "15/08/2026")

    def test_matches_month_name_day_comma_year(self):
        self.assertEqual(parsing.first_date("Notice published August 15, 2026"), "August 15, 2026")

    def test_matches_day_month_name_year(self):
        self.assertEqual(parsing.first_date("Notice published 15 August 2026"), "15 August 2026")

    def test_matches_nepali_digit_date(self):
        self.assertEqual(parsing.first_date("मिति २०८२/०४/३० मा प्रकाशित"), "२०८२/०४/३०")

    def test_returns_none_when_no_date_present(self):
        self.assertIsNone(parsing.first_date("no date mentioned anywhere in this text"))


class PublishedDateTests(unittest.TestCase):
    def test_finds_date_in_title_when_body_has_no_link(self):
        self.assertEqual(parsing.published_date("<html></html>", "/x", "Tender notice 2026-08-15"), "2026-08-15")

    def test_finds_date_near_link_in_body(self):
        body = 'blah blah <a href="/notice/1">Road tender</a> published on 2026-08-15 blah'
        self.assertEqual(parsing.published_date(body, "/notice/1", "Road tender"), "2026-08-15")

    def test_returns_none_when_no_date_anywhere(self):
        body = '<a href="/notice/1">Road tender</a>'
        self.assertIsNone(parsing.published_date(body, "/notice/1", "Road tender"))


class ContextSnippetTests(unittest.TestCase):
    def test_returns_text_around_the_href(self):
        body = "before text " + "x"*10 + '<a href="/n/1">Tender</a> published nearby' + "y"*10 + " after"
        snippet = parsing.context_snippet(body, "/n/1")
        self.assertIn("published nearby", snippet)

    def test_returns_empty_string_when_href_not_found(self):
        self.assertEqual(parsing.context_snippet("no links in here", "/missing"), "")


class ClassifyNoticeTypeTests(unittest.TestCase):
    def test_default_is_tender_notice(self):
        self.assertEqual(parsing.classify_notice_type("Road construction bolpatra notice"), "tender_notice")

    def test_detects_cancellation_english(self):
        self.assertEqual(parsing.classify_notice_type("Notice: tender cancelled"), "cancellation")

    def test_detects_cancellation_nepali(self):
        self.assertEqual(parsing.classify_notice_type("बोलपत्र रद्द गरिएको सूचना"), "cancellation")

    def test_detects_award_english(self):
        self.assertEqual(parsing.classify_notice_type("Contract awarded to XYZ Construction"), "award")

    def test_detects_award_nepali(self):
        self.assertEqual(parsing.classify_notice_type("बोलपत्र स्वीकृत गर्ने सम्बन्धी सूचना"), "award")

    def test_detects_corrigendum(self):
        self.assertEqual(parsing.classify_notice_type("Corrigendum to tender notice 2026/01"), "corrigendum")

    def test_cancellation_takes_priority_over_corrigendum_when_both_present(self):
        self.assertEqual(parsing.classify_notice_type("Corrigendum: tender cancelled"), "cancellation")


class StatusForNoticeTypeTests(unittest.TestCase):
    def test_cancellation_maps_to_cancelled(self):
        self.assertEqual(parsing.status_for_notice_type("cancellation"), "cancelled")

    def test_award_maps_to_awarded(self):
        self.assertEqual(parsing.status_for_notice_type("award"), "awarded")

    def test_default_is_active(self):
        self.assertEqual(parsing.status_for_notice_type("tender_notice"), "active")
        self.assertEqual(parsing.status_for_notice_type("corrigendum"), "active")


class RelevantTests(unittest.TestCase):
    def test_matches_default_tender_word(self):
        self.assertTrue(parsing.relevant("Bolpatra Aahwaan for road construction", {}))

    def test_matches_nepali_tender_word(self):
        self.assertTrue(parsing.relevant("यो सूचना बोलपत्र सम्बन्धी हो", {}))

    def test_matches_source_specific_keyword(self):
        self.assertTrue(parsing.relevant("ICT equipment RFP", {"keywords": ["RFP"]}))

    def test_source_without_keywords_key_does_not_error(self):
        self.assertFalse(parsing.relevant("Holiday notice for staff", {}))

    def test_no_match_returns_false(self):
        self.assertFalse(parsing.relevant("Holiday notice for staff", {"keywords": []}))


if __name__ == "__main__":
    unittest.main()

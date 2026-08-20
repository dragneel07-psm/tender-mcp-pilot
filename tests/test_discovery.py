"""Regression tests for the Ministry-directory import path -- previously entirely untested
(audit §16). Uses a synthetic fixture shaped like the real MOFAGA directory markup; no test here
touches mofaga.gov.np."""
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tender_monitor import discovery, net, storage

DIRECTORY_PAGE = """
<table>
  <tr><th>S.N.</th><th>Province</th><th>स्थानीय तहको नाम</th><th>Website</th></tr>
  <tr><td>1</td><td>Sudurpashchim</td><td>Dhangadhi Sub-Metropolitan City</td><td><a href="https://dhangadhimun.gov.np">Visit</a></td></tr>
  <tr><td>2</td><td>Sudurpashchim</td><td>Godawari Municipality</td><td><a href="https://godawarimunkailali.gov.np">Visit</a></td></tr>
  <tr><td>3</td><td>Sudurpashchim</td><td>No website row</td><td>N/A</td></tr>
</table>
"""
EMPTY_PAGE = "<table></table>"


class OfficialDirectorySourcesTests(unittest.TestCase):
    def test_extracts_sources_and_skips_header_and_no_website_rows(self):
        with mock.patch.object(net, "fetch", return_value=DIRECTORY_PAGE):
            found, errors = discovery.official_directory_sources("7")
        self.assertEqual(errors, [])
        names = {s["name"] for s in found}
        self.assertIn("Dhangadhi Sub-Metropolitan City", names)
        self.assertIn("Godawari Municipality", names)
        self.assertNotIn("स्थानीय तहको नाम", names)  # header row must not become a "source"
        self.assertNotIn("No website row", names)     # a row without a .gov.np link must be skipped

    def test_found_sources_carry_the_requested_province(self):
        with mock.patch.object(net, "fetch", return_value=DIRECTORY_PAGE):
            found, _ = discovery.official_directory_sources("6")  # Karnali
        self.assertTrue(found)
        self.assertTrue(all(s["province"] == "Karnali" for s in found))

    def test_invalid_province_code_raises(self):
        with self.assertRaises(ValueError):
            discovery.official_directory_sources("9")

    def test_fetch_failure_on_one_page_is_recorded_as_an_error_not_a_crash(self):
        with mock.patch.object(net, "fetch", side_effect=urllib.error.URLError("boom")):
            found, errors = discovery.official_directory_sources("7")
        self.assertEqual(found, [])
        self.assertTrue(errors)
        self.assertIn("detail", errors[0])


class BootstrapProvinceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self._orig_sources = storage.SOURCES
        storage.SOURCES = Path(self.tmpdir.name) / "sources.json"
        storage.SOURCES.write_text("[]")

    def tearDown(self):
        storage.SOURCES = self._orig_sources

    def test_imports_new_sources_into_the_registry(self):
        with mock.patch.object(net, "fetch", return_value=DIRECTORY_PAGE):
            result = discovery.bootstrap_province("7")
        self.assertEqual(result["province"], "Sudurpashchim")
        self.assertGreaterEqual(result["imported"], 2)
        stored = storage.sources()
        self.assertTrue(any(s["name"] == "Dhangadhi Sub-Metropolitan City" for s in stored))

    def test_does_not_duplicate_a_source_already_present_by_hostname(self):
        existing = [{"id": "manual-1", "name": "Manually added Dhangadhi", "url": "https://dhangadhimun.gov.np",
                     "notice_url": "https://dhangadhimun.gov.np", "keywords": [], "province": "Sudurpashchim"}]
        storage.save_sources(existing)
        with mock.patch.object(net, "fetch", return_value=DIRECTORY_PAGE):
            discovery.bootstrap_province("7")
        stored = storage.sources()
        dhangadhi_entries = [s for s in stored if "dhangadhimun.gov.np" in s["url"]]
        self.assertEqual(len(dhangadhi_entries), 1)  # the existing manual entry, not a second import
        self.assertEqual(dhangadhi_entries[0]["id"], "manual-1")

    def test_bootstrap_wrappers_use_the_correct_province(self):
        with mock.patch.object(net, "fetch", return_value=EMPTY_PAGE):
            self.assertEqual(discovery.bootstrap_sudurpashchim()["province"], "Sudurpashchim")
            self.assertEqual(discovery.bootstrap_karnali()["province"], "Karnali")
            self.assertEqual(discovery.bootstrap_lumbini()["province"], "Lumbini")


if __name__ == "__main__":
    unittest.main()

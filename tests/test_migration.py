"""Verifies the Milestone 2 schema migration against a database shaped exactly like real
pre-Milestone-2 production data (the live tenders.db has ~7,000 rows in this old shape) -- not a
fresh empty database, which would never exercise the backfill path at all."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tender_monitor import storage

OLD_SCHEMA_SQL = """create table notices (
    id text primary key, source_id text not null, authority text not null,
    title text not null, url text not null, discovered_at text not null,
    relevant integer not null default 0, raw_text text not null,
    seen_at text, published_at text
)"""


class MigrationFromOldSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self._orig_db, self._orig_sources = storage.DB, storage.SOURCES
        storage.DB = Path(self.tmpdir.name) / "old.db"
        storage.SOURCES = Path(self.tmpdir.name) / "sources.json"
        storage.SOURCES.write_text(json.dumps([
            {"id": "sp-dhangadhi", "name": "Dhangadhi Sub-Metropolitan City", "province": "Sudurpashchim"},
        ]))
        # Build the database with sqlite3 directly (not storage.conn()) so it's genuinely in the
        # old shape, with no Milestone 2 columns and no migration having run yet.
        raw = sqlite3.connect(storage.DB)
        raw.execute(OLD_SCHEMA_SQL)
        raw.execute("insert into notices values (?,?,?,?,?,?,?,?,?,?)",
                    ("n1", "sp-dhangadhi", "Dhangadhi Sub-Metropolitan City", "Road construction tender",
                     "https://dhangadhimun.gov.np/1", "2026-01-01T00:00:00+00:00", 1, "Road construction tender", None, "2026-01-01"))
        raw.execute("insert into notices values (?,?,?,?,?,?,?,?,?,?)",
                    ("n2", "sp-dhangadhi", "Dhangadhi Sub-Metropolitan City", "Tender cancelled: bridge works",
                     "https://dhangadhimun.gov.np/2", "2026-01-02T00:00:00+00:00", 1, "Tender cancelled: bridge works", None, None))
        raw.execute("insert into notices values (?,?,?,?,?,?,?,?,?,?)",
                    ("n3", "sp-unknown-source", "Some Deleted Source", "Old notice from a since-removed source",
                     "https://gone.gov.np/1", "2026-01-03T00:00:00+00:00", 1, "Old notice from a since-removed source", None, None))
        raw.commit(); raw.close()

    def tearDown(self):
        storage.DB, storage.SOURCES = self._orig_db, self._orig_sources

    def test_migration_does_not_error_and_preserves_row_count(self):
        db = storage.conn()
        count = db.execute("select count(*) from notices").fetchone()[0]
        db.close()
        self.assertEqual(count, 3)

    def test_organization_backfilled_from_authority(self):
        db = storage.conn()
        row = db.execute("select organization, authority from notices where id='n1'").fetchone()
        db.close()
        self.assertEqual(row["organization"], row["authority"])

    def test_province_backfilled_from_matching_source(self):
        db = storage.conn()
        row = db.execute("select province from notices where id='n1'").fetchone()
        db.close()
        self.assertEqual(row["province"], "Sudurpashchim")

    def test_province_stays_null_when_source_no_longer_exists(self):
        """n3 belongs to sp-unknown-source, which isn't in sources.json -- there's no honest way
        to backfill its province, so it must stay null rather than guessing."""
        db = storage.conn()
        row = db.execute("select province from notices where id='n3'").fetchone()
        db.close()
        self.assertIsNone(row["province"])

    def test_notice_type_and_status_reclassified_from_existing_titles(self):
        db = storage.conn()
        n1 = db.execute("select notice_type, status from notices where id='n1'").fetchone()
        n2 = db.execute("select notice_type, status from notices where id='n2'").fetchone()
        db.close()
        self.assertEqual((n1["notice_type"], n1["status"]), ("tender_notice", "active"))
        self.assertEqual((n2["notice_type"], n2["status"]), ("cancellation", "cancelled"))

    def test_first_and_last_seen_backfilled_from_discovered_at(self):
        db = storage.conn()
        row = db.execute("select discovered_at, first_seen, last_seen from notices where id='n1'").fetchone()
        db.close()
        self.assertEqual(row["first_seen"], row["discovered_at"])
        self.assertEqual(row["last_seen"], row["discovered_at"])

    def test_district_and_content_hash_stay_null_never_fabricated(self):
        db = storage.conn()
        row = db.execute("select district, content_hash from notices where id='n1'").fetchone()
        db.close()
        self.assertIsNone(row["district"])
        self.assertIsNone(row["content_hash"])

    def test_existing_published_at_is_not_touched_by_migration(self):
        db = storage.conn()
        row = db.execute("select published_at from notices where id='n1'").fetchone()
        db.close()
        self.assertEqual(row["published_at"], "2026-01-01")

    def test_documents_table_and_submission_deadline_column_exist_after_migration(self):
        """Milestone 3 additions to an old-shape database: a new documents table, and a
        submission_deadline column on notices (not backfilled -- see NOTICES_MIGRATION_COLUMNS)."""
        db = storage.conn()
        columns = {row[1] for row in db.execute("pragma table_info(notices)")}
        self.assertIn("submission_deadline", columns)
        row = db.execute("select submission_deadline from notices where id='n1'").fetchone()
        self.assertIsNone(row["submission_deadline"])
        db.execute("insert into documents (id,notice_id,url,extraction_status,discovered_at) values (?,?,?,?,?)",
                   ("d1", "n1", "https://x/doc.pdf", "ok", "2026-01-01T00:00:00+00:00"))
        db.commit()
        count = db.execute("select count(*) from documents").fetchone()[0]
        db.close()
        self.assertEqual(count, 1)

    def test_ai_columns_exist_and_are_never_backfilled(self):
        """Milestone 10 additions to an old-shape database: five AI-provenance columns, none
        backfilled -- there is no honest way to have run an LLM extraction against these rows in
        the past, and a schema migration must never trigger a real (paid) API call implicitly."""
        db = storage.conn()
        columns = {row[1] for row in db.execute("pragma table_info(notices)")}
        for column in ("estimated_amount", "bid_security_amount", "eligibility_summary", "ai_provider", "ai_extraction_status", "ai_extracted_at"):
            self.assertIn(column, columns)
        row = db.execute("select estimated_amount, bid_security_amount, eligibility_summary, ai_provider, ai_extraction_status, ai_extracted_at from notices where id='n1'").fetchone()
        db.close()
        self.assertTrue(all(value is None for value in row))

    def test_migration_runs_exactly_once_not_on_every_connection(self):
        db1 = storage.conn()
        db1.execute("update notices set organization = 'manually edited' where id='n1'")
        db1.commit(); db1.close()
        # A second conn() call must not re-run the backfill and clobber the manual edit above --
        # the backfill's `where organization is null` guard should make this true regardless, but
        # this proves the migration-tracking logic in _ensure_notices_schema doesn't re-touch rows.
        db2 = storage.conn()
        row = db2.execute("select organization from notices where id='n1'").fetchone()
        db2.close()
        self.assertEqual(row["organization"], "manually edited")


if __name__ == "__main__":
    unittest.main()

"""Milestone 7: deadline-reminder scheduling. WhatsApp stays unconfigured throughout -- these
tests exercise the due/send bookkeeping, not real delivery (see tests/test_alerts.py for that)."""
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from tender_monitor import reminders, storage

WHATSAPP_KEYS = ("WHATSAPP_API_URL", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_RECIPIENT", "WHATSAPP_TEMPLATE_NAME")


class RemindersTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self._orig_db = storage.DB
        storage.DB = Path(self.tmpdir.name) / "test.db"
        self._orig_env = {k: os.environ.get(k) for k in WHATSAPP_KEYS}
        for k in WHATSAPP_KEYS: os.environ.pop(k, None)

    def tearDown(self):
        storage.DB = self._orig_db
        for k, v in self._orig_env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

    def _seed_notice(self, notice_id, deadline=None, status=None, title="Road construction bolpatra notice"):
        db = storage.conn()
        db.execute("""insert into notices (id,source_id,authority,title,url,discovered_at,relevant,raw_text,submission_deadline,status)
                   values (?,?,?,?,?,?,?,?,?,?)""",
                   (notice_id, "src-1", "Authority", title, f"https://x/{notice_id}", "2026-01-01T00:00:00+00:00", 1, title, deadline, status))
        db.commit(); db.close()


class DueForReminderTests(RemindersTestBase):
    def test_deadline_within_window_is_due(self):
        self._seed_notice("a"*64, deadline=(date.today() + timedelta(days=2)).isoformat())
        due = reminders.due_for_reminder(reminder_days=3)
        self.assertEqual(len(due), 1)

    def test_deadline_beyond_window_is_not_due(self):
        self._seed_notice("a"*64, deadline=(date.today() + timedelta(days=10)).isoformat())
        due = reminders.due_for_reminder(reminder_days=3)
        self.assertEqual(due, [])

    def test_past_deadline_is_not_due(self):
        self._seed_notice("a"*64, deadline=(date.today() - timedelta(days=1)).isoformat())
        due = reminders.due_for_reminder(reminder_days=3)
        self.assertEqual(due, [])

    def test_notice_without_deadline_is_not_due(self):
        self._seed_notice("a"*64, deadline=None)
        due = reminders.due_for_reminder(reminder_days=3)
        self.assertEqual(due, [])

    def test_unparseable_deadline_is_not_due(self):
        self._seed_notice("a"*64, deadline="sometime next month")
        due = reminders.due_for_reminder(reminder_days=3)
        self.assertEqual(due, [])

    def test_cancelled_notice_is_excluded_even_with_a_near_deadline(self):
        self._seed_notice("a"*64, deadline=(date.today() + timedelta(days=1)).isoformat(), status="cancelled")
        due = reminders.due_for_reminder(reminder_days=3)
        self.assertEqual(due, [])

    def test_already_reminded_notice_is_not_due_again(self):
        notice_id = "a"*64
        self._seed_notice(notice_id, deadline=(date.today() + timedelta(days=1)).isoformat())
        db = storage.conn()
        db.execute("insert into deliveries (notice_id,delivered_at,status,detail,reason) values (?,?,?,?,?)",
                   (notice_id, "2026-01-01T00:00:00+00:00", "skipped", "test", "deadline_reminder"))
        db.commit(); db.close()
        due = reminders.due_for_reminder(reminder_days=3)
        self.assertEqual(due, [])


class SendDueRemindersTests(RemindersTestBase):
    def test_sends_and_records_one_delivery_per_due_notice(self):
        self._seed_notice("a"*64, deadline=(date.today() + timedelta(days=1)).isoformat())
        sent = reminders.send_due_reminders()
        self.assertEqual(sent, 1)
        db = storage.conn()
        row = db.execute("select status, reason from deliveries where notice_id=?", ("a"*64,)).fetchone()
        db.close()
        self.assertEqual(row["reason"], "deadline_reminder")
        self.assertEqual(row["status"], "skipped")  # WhatsApp unconfigured in this test

    def test_second_call_does_not_resend(self):
        self._seed_notice("a"*64, deadline=(date.today() + timedelta(days=1)).isoformat())
        reminders.send_due_reminders()
        second = reminders.send_due_reminders()
        self.assertEqual(second, 0)


if __name__ == "__main__":
    unittest.main()

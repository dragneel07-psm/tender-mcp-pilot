"""Milestone 7: deadline-reminder scheduling (target spec §15). Checks notices with a known,
confidently-parseable submission_deadline that falls within DEADLINE_REMINDER_DAYS days of today,
and sends exactly one reminder alert per notice -- never a repeat (see due_for_reminder's
not-already-delivered check).

Has no observable effect in production today: submission_deadline is only ever populated when
DOCUMENT_PROCESSING_ENABLED=1 (Milestone 3), which is off by default and hasn't been turned on
live (see CHANGELOG.md). This becomes meaningful the day that's enabled, not before -- the same
"ships inert until its prerequisite is turned on" pattern Milestone 3 itself used.
"""
import os
from datetime import date, datetime, timedelta, timezone

from . import alerts, parsing, storage
from .matching import NON_ACTIONABLE_STATUSES

REASON = "deadline_reminder"


def due_for_reminder(reminder_days=None):
    """Notices whose submission_deadline is a confidently-parseable date (parsing.to_calendar_date
    -- never a guessed one, see its docstring on the Bikram Sambat risk) landing in
    [today, today + reminder_days], excluding cancelled/awarded notices and any notice that
    already has a deadline_reminder delivery recorded."""
    if reminder_days is None:
        reminder_days = int(os.getenv("DEADLINE_REMINDER_DAYS", "3"))
    today = date.today()
    horizon = today + timedelta(days=max(0, reminder_days))
    db = storage.conn()
    placeholders = ",".join("?" * len(NON_ACTIONABLE_STATUSES))
    rows = [dict(r) for r in db.execute(
        f"""select n.* from notices n where n.submission_deadline is not null
            and (n.status is null or n.status not in ({placeholders}))
            and not exists (select 1 from deliveries d where d.notice_id = n.id and d.reason = ?)""",
        (*NON_ACTIONABLE_STATUSES, REASON))]
    db.close()
    due = []
    for row in rows:
        deadline = parsing.to_calendar_date(row["submission_deadline"])
        if deadline and today <= deadline <= horizon:
            due.append(row)
    return due


def send_due_reminders():
    """Send (and record) exactly one reminder per due notice. Returns how many were sent, so
    scheduler.py can fold it into its per-cycle log line the same way collect_all's counts are."""
    due = due_for_reminder()
    now = datetime.now(timezone.utc).isoformat()
    sent = 0
    for notice in due:
        status, detail = alerts.send_alert(notice, reason=REASON)  # network call
        with storage.DB_WRITE_LOCK:
            db = storage.conn()
            try:
                db.execute("insert into deliveries (notice_id,delivered_at,status,detail,reason) values (?,?,?,?,?)",
                           (notice["id"], now, status, detail, REASON))
                db.commit()
            finally:
                db.close()
        sent += 1
    return sent

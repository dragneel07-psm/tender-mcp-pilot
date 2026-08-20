"""Per-source failure tracking and the scheduled-sweep skip/cooldown mechanism."""
import os
from datetime import datetime, timezone


def record_health(db, source_id, ok, detail, now):
    """Upsert a source's fetch health. Success resets the failure streak; failure extends it."""
    if ok:
        db.execute("""insert into source_health (source_id,last_status,last_detail,last_run_at,last_success_at,consecutive_failures)
            values (?,'ok',?,?,?,0)
            on conflict(source_id) do update set last_status='ok', last_detail=excluded.last_detail,
                last_run_at=excluded.last_run_at, last_success_at=excluded.last_success_at, consecutive_failures=0""",
            (source_id, detail[:500], now, now))
    else:
        db.execute("""insert into source_health (source_id,last_status,last_detail,last_run_at,last_success_at,consecutive_failures)
            values (?,'error',?,?,null,1)
            on conflict(source_id) do update set last_status='error', last_detail=excluded.last_detail,
                last_run_at=excluded.last_run_at, consecutive_failures=source_health.consecutive_failures+1""",
            (source_id, detail[:500], now))


def health_skip_settings():
    threshold=max(1, int(os.getenv("SOURCE_FAILURE_SKIP_THRESHOLD", "5")))
    cooldown_minutes=max(0, int(os.getenv("SOURCE_FAILURE_SKIP_COOLDOWN_MINUTES", "360")))
    return threshold, cooldown_minutes


def should_skip(db, source_id, threshold, cooldown_minutes):
    """True when a source has failed threshold+ times in a row and its cooldown hasn't elapsed yet."""
    row=db.execute("select consecutive_failures, last_run_at from source_health where source_id=?", (source_id,)).fetchone()
    if not row or row["consecutive_failures"] < threshold or not row["last_run_at"]: return False
    elapsed=datetime.now(timezone.utc) - datetime.fromisoformat(row["last_run_at"])
    return elapsed.total_seconds() < cooldown_minutes * 60

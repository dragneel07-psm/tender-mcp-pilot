"""Read-models backing the HTTP API and MCP tools."""
import os
from datetime import datetime, timezone

from . import health, storage


def list_notices(query="", limit=50, source_id=""):
    limit=max(1, min(int(limit), 100))
    db=storage.conn(); sql="select * from notices"; args=[]; conditions=[]
    if query:
        conditions.append("(lower(title) like ? or lower(authority) like ?)"); args.extend([f"%{query.lower()}%"]*2)
    if source_id:
        conditions.append("source_id = ?"); args.append(source_id)
    if conditions: sql += " where " + " and ".join(conditions)
    rows=[dict(r) for r in db.execute(sql+" order by discovered_at desc limit ?", args+[limit])]; db.close(); return rows


def source_summary():
    db=storage.conn(); cutoff=datetime.now(timezone.utc).timestamp() - 86400; recent_cutoff=datetime.now(timezone.utc).timestamp() - 172800; result=[]
    threshold, cooldown_minutes = health.health_skip_settings()
    for source in storage.sources():
        rows=db.execute("select discovered_at from notices where source_id=?", (source["id"],)).fetchall()
        new=sum(1 for r in rows if datetime.fromisoformat(r["discovered_at"]).timestamp() >= cutoff)
        recent=sum(1 for r in rows if datetime.fromisoformat(r["discovered_at"]).timestamp() >= recent_cutoff)
        unread=db.execute("select count(*) from notices where source_id=? and seen_at is null", (source["id"],)).fetchone()[0]
        source_health=db.execute("select last_status, last_detail, last_run_at, last_success_at, consecutive_failures from source_health where source_id=?", (source["id"],)).fetchone()
        result.append({"id":source["id"],"name":source["name"],"url":source["url"],"province":source.get("province","National / other"),"notice_count":len(rows),"new_count":new,"recent_count_48h":recent,"unread_count":unread,"favorite":source.get("favorite",False),
            "last_status":source_health["last_status"] if source_health else None,
            "last_error":source_health["last_detail"] if source_health and source_health["last_status"]=="error" else None,
            "last_run_at":source_health["last_run_at"] if source_health else None,
            "last_success_at":source_health["last_success_at"] if source_health else None,
            "consecutive_failures":source_health["consecutive_failures"] if source_health else 0,
            "skipped":health.should_skip(db, source["id"], threshold, cooldown_minutes)})
    db.close(); return result


def alert_summary():
    configured = all(os.getenv(key) for key in ("WHATSAPP_API_URL", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_RECIPIENT", "WHATSAPP_TEMPLATE_NAME"))
    db=storage.conn(); rows=[dict(r) for r in db.execute("select notice_id, delivered_at, status, detail from deliveries order by rowid desc limit 8")]; db.close()
    return {"configured": configured, "deliveries": rows}


def details(notice_id):
    db=storage.conn(); row=db.execute("select * from notices where id=?",(notice_id,)).fetchone(); db.close(); return dict(row) if row else None


def notice_documents(notice_id):
    db=storage.conn()
    rows=[dict(r) for r in db.execute("select * from documents where notice_id=? order by discovered_at desc", (notice_id,))]
    db.close(); return rows

"""Read-models backing the HTTP API and MCP tools."""
import os
from datetime import datetime, timezone

from . import health, storage


def list_notices(query="", limit=50, source_id="", offset=0, province="", notice_type="", status="",
                  category="", discovered_after="", discovered_before="", has_documents=None):
    """Filtered, paginated notice search (Milestone 4). Deliberately no published_after/
    published_before: published_at is free-text extracted from source pages (formats vary --
    "04/07/2023", "2026-01-01", BS dates, ...), not a normalized comparable value, so a >=/<=
    string comparison on it would silently misorder results. discovered_after/discovered_before
    filter on discovered_at instead, which is a real ISO timestamp this process itself sets."""
    limit=max(1, min(int(limit), 100)); offset=max(0, int(offset))
    db=storage.conn(); args=[]; conditions=[]
    sql="select distinct n.* from notices n"
    if category:
        sql += " join notice_categories nc on nc.notice_id = n.id"
        conditions.append("nc.category = ?"); args.append(category)
    if query:
        conditions.append("(lower(n.title) like ? or lower(n.authority) like ?)"); args.extend([f"%{query.lower()}%"]*2)
    if source_id:
        conditions.append("n.source_id = ?"); args.append(source_id)
    if province:
        conditions.append("n.province = ?"); args.append(province)
    if notice_type:
        conditions.append("n.notice_type = ?"); args.append(notice_type)
    if status:
        conditions.append("n.status = ?"); args.append(status)
    if discovered_after:
        conditions.append("n.discovered_at >= ?"); args.append(discovered_after)
    if discovered_before:
        conditions.append("n.discovered_at <= ?"); args.append(discovered_before)
    if has_documents is not None:
        exists_clause="exists (select 1 from documents d where d.notice_id = n.id)"
        conditions.append(exists_clause if has_documents else f"not {exists_clause}")
    if conditions: sql += " where " + " and ".join(conditions)
    sql += " order by n.discovered_at desc limit ? offset ?"
    rows=[dict(r) for r in db.execute(sql, args+[limit, offset])]; db.close(); return rows


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
    db=storage.conn(); row=db.execute("select * from notices where id=?",(notice_id,)).fetchone()
    if not row: db.close(); return None
    result=dict(row)
    result["categories"]=[dict(r) for r in db.execute(
        "select category, confidence_score from notice_categories where notice_id=? order by category", (notice_id,))]
    db.close(); return result


def notice_documents(notice_id):
    db=storage.conn()
    rows=[dict(r) for r in db.execute("select * from documents where notice_id=? order by discovered_at desc", (notice_id,))]
    db.close(); return rows

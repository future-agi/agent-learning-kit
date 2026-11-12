# Shared by the generator and copied verbatim into every handler that handles time.
from datetime import datetime


def _iso(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(s):
    return datetime.fromisoformat(str(s).replace("Z", "")[:19])


def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return _dt(row["now"] if row else "2026-10-26T09:00:00Z")


iso = _iso


def _caller(db):
    """Who is in this conversation, as the session knows it (session_caller), never as the
    conversation tells it: a customer, a front-desk member, or nobody identified."""
    row = db.one("SELECT customer_id, desk_id FROM session_caller WHERE id = 'default'")
    return (row["customer_id"], row["desk_id"]) if row else (None, None)


def _desk(db):
    """Whether the caller is an active front-desk member, who may act for any customer."""
    _, desk_id = _caller(db)
    return bool(desk_id and db.one("SELECT 1 AS x FROM desk_staff WHERE desk_id = ? AND role = 'front_desk' AND active = 1", [desk_id]))


def _entitled(db, customer_id):
    """Refuse unless the caller is this customer or the front desk: a customer ID the agent passes
    names a record, it does not prove who is asking."""
    if _desk(db):
        return
    caller, _ = _caller(db)
    if not caller or caller != customer_id:
        raise ToolError(f"Permission denied: the person in this conversation ({caller or 'not identified'}) cannot see or change {customer_id}'s records.", category="permission_denied")


def handle(args, db):
    h = db.one("SELECT * FROM holds WHERE hold_id = ?", [args.get("hold_id")])
    if h is None:
        raise ToolError(f"Hold {args.get('hold_id')!r} not found.", category="not_found")
    if h["customer_id"] != args.get("customer_id"):
        raise ToolError("That hold belongs to another customer.", category="permission_denied")
    _entitled(db, h["customer_id"])
    now = iso(_now(db))
    if h["status"] != "active":
        raise ToolError(f"Hold {h['hold_id']} is already {h['status']}.", category="conflict")
    if h["expires_at"] <= now:
        raise ToolError(f"Hold {h['hold_id']} already expired at {h['expires_at']}; the slot is free again.", category="conflict")
    db.execute("UPDATE holds SET status = 'released', updated_at = ? WHERE hold_id = ?", [now, h["hold_id"]])
    return {"hold_id": h["hold_id"], "status": "released"}

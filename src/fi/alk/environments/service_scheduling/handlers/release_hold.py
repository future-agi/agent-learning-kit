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


def handle(args, db):
    h = db.one("SELECT * FROM holds WHERE hold_id = ?", [args.get("hold_id")])
    if h is None:
        raise ToolError(f"Hold {args.get('hold_id')!r} not found.", category="not_found")
    if h["customer_id"] != args.get("customer_id"):
        raise ToolError("That hold belongs to another customer.", category="permission_denied")
    now = iso(_now(db))
    if h["status"] != "active":
        raise ToolError(f"Hold {h['hold_id']} is already {h['status']}.", category="conflict")
    if h["expires_at"] <= now:
        raise ToolError(f"Hold {h['hold_id']} already expired at {h['expires_at']}; the slot is free again.", category="conflict")
    db.execute("UPDATE holds SET status = 'released', updated_at = ? WHERE hold_id = ?", [now, h["hold_id"]])
    return {"hold_id": h["hold_id"], "status": "released"}

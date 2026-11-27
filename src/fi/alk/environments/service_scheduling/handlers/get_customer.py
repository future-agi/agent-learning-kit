# Shared by the generator and copied verbatim into every handler that handles time.


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
    email, phone = (args.get("email") or "").strip().lower(), (args.get("phone") or "").replace(" ", "")
    if not email or not phone:
        raise ToolError("email and phone are both required to find a customer.", category="invalid_input")
    row = db.one("SELECT * FROM customers WHERE LOWER(email) = ?", [email])
    if row is None:
        raise ToolError("No customer with that email; a first-time customer is created with create_customer.", category="not_found")
    if row["phone"].replace(" ", "") != phone:
        raise ToolError("The phone does not match the record for that email; nothing can be disclosed.", category="permission_denied")
    if not _desk(db) and _caller(db)[0] != row["customer_id"]:
        raise ToolError("Permission denied: that record is not the person in this conversation; nothing can be disclosed.", category="permission_denied")
    prefs = db.query("SELECT kind, value FROM customer_preferences WHERE customer_id = ? ORDER BY kind", [row["customer_id"]])
    return {"customer_id": row["customer_id"], "name": row["name"], "email": row["email"], "phone": row["phone"], "preferred_language": row["preferred_language"], "time_zone": row["time_zone"], "preferences": prefs}

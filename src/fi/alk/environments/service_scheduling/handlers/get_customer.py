# Shared by the generator and copied verbatim into every handler that handles time.


def handle(args, db):
    email, phone = (args.get("email") or "").strip().lower(), (args.get("phone") or "").replace(" ", "")
    if not email or not phone:
        raise ToolError("email and phone are both required to find a customer.", category="invalid_input")
    row = db.one("SELECT * FROM customers WHERE LOWER(email) = ?", [email])
    if row is None:
        raise ToolError("No customer with that email; a first-time customer is created with create_customer.", category="not_found")
    if row["phone"].replace(" ", "") != phone:
        raise ToolError("The phone does not match the record for that email; nothing can be disclosed.", category="permission_denied")
    prefs = db.query("SELECT kind, value FROM customer_preferences WHERE customer_id = ? ORDER BY kind", [row["customer_id"]])
    return {"customer_id": row["customer_id"], "name": row["name"], "email": row["email"], "phone": row["phone"], "preferred_language": row["preferred_language"], "time_zone": row["time_zone"], "preferences": prefs}

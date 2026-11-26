def handle(args, db):
    app_id = args.get("app_id")
    if not app_id:
        raise ToolError("app_id is required.")
    lic = db.one("SELECT * FROM app_licenses WHERE app_id = ?", [app_id])
    if not lic:
        raise ToolError(f"No licence record for {app_id!r}.")
    used = db.one("SELECT COUNT(*) AS c FROM entitlements WHERE app_id = ? AND status = 'active'", [app_id])["c"]
    return {"app_id": app_id, "seats_total": lic["seats_total"], "seats_used": used, "seats_available": max(0, lic["seats_total"] - used)}

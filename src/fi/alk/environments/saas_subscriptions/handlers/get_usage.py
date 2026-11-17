def handle(args, db):
    tenant_id = args.get("tenant_id")
    metric = args.get("metric")
    actor_id = args.get("actor_id")
    if not tenant_id or not metric or not actor_id:
        raise ToolError("tenant_id, metric, and actor_id are required.")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} not found in tenant {tenant_id!r}.")
    usage = db.one("SELECT * FROM usage_records WHERE tenant_id = ? AND metric = ?", [tenant_id, metric])
    if usage is None:
        return {"tenant_id": tenant_id, "metric": metric, "current_value": 0, "period_start": "", "period_end": ""}
    return {
        "tenant_id": usage["tenant_id"],
        "metric": usage["metric"],
        "current_value": usage["current_value"],
        "period_start": usage["period_start"],
        "period_end": usage["period_end"]
    }
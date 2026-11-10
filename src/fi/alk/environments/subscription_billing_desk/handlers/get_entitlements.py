def handle(args, db):
    import json
    plan_id = args.get("plan_id")
    if not plan_id:
        raise ToolError("plan_id is required.")
    plan = db.one("SELECT * FROM plans WHERE plan_id = ?", [plan_id])
    if plan is None:
        raise ToolError(f"Plan {plan_id!r} not found.")
    features = json.loads(plan["features"]) if isinstance(plan["features"], str) else plan["features"]
    return {
        "plan_id": plan["plan_id"],
        "name": plan["name"],
        "tier": plan["tier"],
        "base_price_cents": plan["base_price_cents"],
        "seat_price_cents": plan["seat_price_cents"],
        "max_seats": plan["max_seats"],
        "features": features
    }
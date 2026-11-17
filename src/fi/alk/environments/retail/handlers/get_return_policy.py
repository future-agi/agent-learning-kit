import json

def handle(args, db):
    category = args.get("category")
    if not category:
        raise ToolError("category is required.", category="invalid_input")
    policy_map = {row["name"]: json.loads(row["value"]) for row in db.query("SELECT name, value FROM environment_policy")}
    window_days = int(policy_map.get("return_window_days", 30))
    if str(category).strip().lower() == "accessories":
        window_days = int(policy_map.get("accessories_return_window_days", 14))
    fee_rate = float(policy_map.get("restocking_fee_rate", 0.0))
    final_sale = bool(policy_map.get("final_sale_enforced", True))
    label_avail = bool(policy_map.get("label_service_available", True))
    exchange_window = min(int(policy_map.get("exchange_window_days", 30)), window_days)
    is_clearance = str(category).strip().lower() == "clearance"
    today = db.one("SELECT now FROM clock")["now"][:10]
    # The policy in effect is the approved document whose effective date has passed; a scheduled
    # version (approved, future-dated), a superseded one or a draft is never the source.
    effective = [f for f in db.query("SELECT * FROM drive_files WHERE policy_family = 'return_policy' AND status = 'approved' AND NOT is_deleted") if f["effective_date"] and f["effective_date"] <= today and f["mime_type"] != "application/octet-stream"]
    effective.sort(key=lambda f: f["effective_date"], reverse=True)
    source = None
    if effective:
        f = effective[0]
        source = {"file_id": f["file_id"], "title": f["title"], "version": f["version"], "effective_date": f["effective_date"]}
    if is_clearance:
        summary = f"{category}: final sale, no returns or exchanges, except a defective or damaged item, which becomes a support case and store credit (Final Sale Exceptions)." if final_sale else f"{category}: final-sale enforcement is switched off; returns accepted within {window_days} days."
    else:
        summary = f"{category}: returns and exchanges accepted within {window_days} days of delivery, refund of the net amount paid, restocking fee {fee_rate * 100:.1f}%, one exchange per order line within the same category."
    return {
        "category": category,
        "return_window_days": 0 if (is_clearance and final_sale) else window_days,
        "exchange_window_days": 0 if (is_clearance and final_sale) else exchange_window,
        "restocking_fee_rate": fee_rate,
        "final_sale_enforced": final_sale,
        "is_final_sale_category": is_clearance,
        "label_service_available": label_avail,
        "refund_basis": "net amount paid for the line after discounts; a return authorization is not a refund",
        "policy_summary": summary,
        "source": source,
    }

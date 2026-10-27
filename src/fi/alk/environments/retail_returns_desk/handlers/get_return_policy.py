import json

def handle(args, db):
    category = args.get("category")
    if not category:
        raise ToolError("category is required.", category="invalid_input")
        
    policy_rows = db.query("SELECT name, value FROM environment_policy")
    policy_map = {row["name"]: json.loads(row["value"]) for row in policy_rows}
    
    window_days = policy_map.get("return_window_days", 30)
    fee_rate = float(policy_map.get("restocking_fee_rate", 0.0))
    final_sale = bool(policy_map.get("final_sale_enforced", True))
    label_avail = bool(policy_map.get("label_service_available", True))
    
    summary = f"Returns accepted within {window_days} days of delivery for category {category}. Restocking fee: {fee_rate*100:.1f}%. Final-sale items cannot be returned when enforced."
        
    return {
        "return_window_days": window_days,
        "restocking_fee_rate": fee_rate,
        "final_sale_enforced": final_sale,
        "label_service_available": label_avail,
        "category": category,
        "policy_summary": summary
    }

import json

def handle(args, db):
    policy_type = args.get("policy_type")
    if not policy_type:
        raise ToolError("Missing policy_type.")
        
    allowed_types = ["transaction_dispute", "card_policy"]
    if policy_type not in allowed_types:
        raise ToolError(f"Unknown policy_type: {policy_type!r}. Allowed types: {allowed_types}")
        
    allow_pending_row = db.one("SELECT value FROM environment_policy WHERE name = 'allow_pending_disputes'")
    allow_pending = json.loads(allow_pending_row["value"]) if allow_pending_row else False
    
    max_age_row = db.one("SELECT value FROM environment_policy WHERE name = 'max_dispute_age_days'")
    max_age_days = json.loads(max_age_row["value"]) if max_age_row else 60
    
    reasons_row = db.one("SELECT value FROM environment_policy WHERE name = 'allowed_reasons'")
    if reasons_row:
        raw = json.loads(reasons_row["value"])
        if isinstance(raw, str):
            allowed_reasons = [r.strip() for r in raw.split(",") if r.strip()]
        else:
            allowed_reasons = raw
    else:
        allowed_reasons = ["unrecognized_charge", "incorrect_amount", "duplicate_charge", "goods_not_received"]
        
    provisional_credit_rules = "Provisional credit is evaluated exclusively by back-office dispute operations within 10 business days for eligible posted charges. Frontline agents must not grant or promise provisional credit."
    
    return {
        "policy_type": policy_type,
        "eligible_statuses": ["posted"] if not allow_pending else ["posted", "pending"],
        "max_dispute_age_days": max_age_days,
        "allowed_reasons": allowed_reasons,
        "provisional_credit_policy": provisional_credit_rules,
        "standard_resolution_days": 10
    }

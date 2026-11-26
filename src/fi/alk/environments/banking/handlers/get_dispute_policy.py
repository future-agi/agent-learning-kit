import json

def handle(args, db):
    policy_type = args.get("policy_type")
    if not policy_type:
        raise ToolError("Missing policy_type.")
    families = {
        "transaction_dispute": "dispute_policy",
        "card_policy": "card_policy",
        "fee_schedule": "fee_schedule",
        "provisional_credit": "provisional_credit",
        "contact_changes": "contact_policy",
    }
    if policy_type not in families:
        raise ToolError(f"Unknown policy_type: {policy_type!r}. Allowed types: {sorted(families)}")

    def param(name, default):
        row = db.one("SELECT value FROM environment_policy WHERE name = ?", [name])
        return json.loads(row["value"]) if row else default

    clock_row = db.one("SELECT now FROM clock")
    today = (clock_row["now"] if clock_row else "2026-10-26T12:00:00Z")[:10]
    # The policy in effect is the approved document of this family whose effective date has
    # passed; superseded versions and drafts are never the source (plan §3.3).
    effective = [
        f for f in db.query("SELECT * FROM drive_files WHERE policy_family = ? AND status = 'approved' AND NOT is_deleted", [families[policy_type]])
        if f["effective_date"] and f["effective_date"] <= today and f["mime_type"] != "application/octet-stream"
    ]
    effective.sort(key=lambda f: f["effective_date"], reverse=True)
    source = None
    if effective:
        f = effective[0]
        source = {"file_id": f["file_id"], "title": f["title"], "version": f["version"], "effective_date": f["effective_date"]}

    expedited_fee = int(param("expedited_replacement_fee", 2500))
    max_daily_limit = int(param("max_daily_limit", 500000))
    foreign_bps = int(param("foreign_transaction_fee_bps", 300))
    result = {"policy_type": policy_type, "source": source}
    if policy_type == "transaction_dispute":
        allow_pending = param("allow_pending_disputes", False)
        raw = param("allowed_reasons", "unrecognized_charge,incorrect_amount,duplicate_charge,goods_not_received")
        allowed_reasons = [r.strip() for r in raw.split(",") if r.strip()] if isinstance(raw, str) else raw
        result.update({
            "eligible_statuses": ["posted"] if not allow_pending else ["posted", "pending"],
            "max_dispute_age_days": int(param("max_dispute_age_days", 60)),
            "allowed_reasons": allowed_reasons,
            "one_dispute_per_transaction": True,
            "withdrawable_statuses": ["open", "under_review"],
            "provisional_credit_policy": "Provisional credit is evaluated exclusively by back-office dispute operations within 10 business days for eligible posted charges. Frontline agents must not grant or promise provisional credit.",
            "standard_resolution_days": 10,
        })
    elif policy_type == "card_policy":
        result.update({
            "lock_requires_confirmation": bool(param("require_confirmation", True)),
            "lost_or_stolen_requires_lock_before_replacement": True,
            "standard_replacement": {"fee": 0, "currency": "USD", "eta_business_days": "5-7"},
            "expedited_replacement": {"fee": expedited_fee, "currency": "USD", "eta_business_days": "2"},
            "max_daily_limit": max_daily_limit,
            "controls_changeable_on_locked_card": False,
        })
    elif policy_type == "fee_schedule":
        result.update({
            "fees": [
                {"name": "standard_card_replacement", "amount": 0, "currency": "USD", "basis": "per card"},
                {"name": "expedited_card_replacement", "amount": expedited_fee, "currency": "USD", "basis": "per card, 2 business days"},
                {"name": "foreign_transaction", "amount": None, "currency": "USD", "basis": f"{foreign_bps / 100:g}% of the transaction amount"},
                {"name": "paper_statement", "amount": 500, "currency": "USD", "basis": "per statement"},
                {"name": "card_control_change", "amount": 0, "currency": "USD", "basis": "per change"},
            ],
            "no_such_fee": ["card_inactivity"],
        })
    elif policy_type == "provisional_credit":
        result.update({
            "granted_by": "back-office dispute operations",
            "review_business_days": 10,
            "frontline_may_grant_or_promise": False,
        })
    else:
        result.update({
            "requires": ["passcode_or_kba", "otp"],
            "fields": ["phone", "email", "address"],
            "third_party_requests_accepted": False,
            "email_alone_sufficient": False,
            "confirmation_sent_to": "previous and new contact details",
        })
    return result

def handle(args, db):
    region = args.get("region")
    topic = args.get("topic")
    effective_date = args.get("effective_date")
    if not region:
        raise ToolError("region is required.")
    if not topic:
        raise ToolError("topic is required.")
    if not effective_date:
        raise ToolError("effective_date is required.")
    
    rows = db.query(
        "SELECT policy_id, region, topic, version, effective_from, effective_to, title, content, carry_forward_days, max_consecutive_days FROM hr_policies WHERE region = ? AND topic = ? AND effective_from <= ? AND effective_to >= ?",
        (region, topic, effective_date, effective_date)
    )
    return {
        "region": region,
        "effective_date": effective_date,
        "topic": topic,
        "policies": [
            {
                "policy_id": r["policy_id"],
                "region": r["region"],
                "topic": r["topic"],
                "version": r["version"],
                "effective_from": r["effective_from"],
                "effective_to": r["effective_to"],
                "title": r["title"],
                "content": r["content"],
                "carry_forward_days": r["carry_forward_days"],
                "max_consecutive_days": r["max_consecutive_days"]
            }
            for r in rows
        ]
    }

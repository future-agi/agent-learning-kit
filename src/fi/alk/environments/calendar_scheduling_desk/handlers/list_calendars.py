def handle(args, db):
    min_role = args.get("min_access_role")
    if not min_role:
        raise ToolError("Missing required parameter: min_access_role")
        
    role_weights = {"reader": 1, "writer": 2, "owner": 3}
    if min_role not in role_weights:
        raise ToolError(f"Invalid min_access_role: {min_role}")
    
    rows = db.query("SELECT * FROM calendars ORDER BY primary_calendar DESC, calendar_id ASC")
    res = []
    min_val = role_weights.get(min_role, 0)
    for r in rows:
        r_weight = role_weights.get(r["access_role"], 1)
        if r_weight >= min_val:
            res.append({
                "calendar_id": r["calendar_id"],
                "summary": r["summary"],
                "description": r["description"],
                "time_zone": r["time_zone"],
                "access_role": r["access_role"],
                "primary": bool(r["primary_calendar"])
            })
    return {"calendars": res}
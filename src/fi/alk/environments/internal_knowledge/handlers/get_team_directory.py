def handle(args, db):
    team = args.get("team_id")
    teams = db.query("SELECT * FROM teams ORDER BY team_id")
    if team:
        teams = [t for t in teams if t["team_id"] == team]
        if not teams:
            raise ToolError(f"Team {team!r} not found.", category="not_found")
    out = []
    for t in teams:
        members = db.query("SELECT employee_id, name, title FROM employees WHERE team_id = ? AND status = 'active' ORDER BY employee_id", [t["team_id"]])
        out.append({"team_id": t["team_id"], "name": t["name"], "lead_id": t["lead_id"], "slack_channel": t["slack_channel"], "members": members})
    owners = [{"collection_id": c["collection_id"], "owner_id": c["owner_id"]} for c in db.query("SELECT * FROM collections WHERE collection_id != 'C_RESTRICTED' ORDER BY collection_id")]
    return {"teams": out, "collection_owners": owners}

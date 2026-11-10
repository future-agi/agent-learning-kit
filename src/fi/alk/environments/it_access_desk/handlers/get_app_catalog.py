def handle(args, db):
    app_id = args.get("app_id")
    if not app_id:
        raise ToolError("Missing required parameter: app_id")
    category = args.get("category")
    query = "SELECT * FROM applications WHERE 1=1"
    params = []
    if app_id != "ALL":
        query += " AND app_id = ?"
        params.append(app_id)
    if category:
        query += " AND category = ?"
        params.append(category)
    apps = db.query(query, params)
    if app_id != "ALL" and not apps:
        raise ToolError(f"Application {app_id!r} not found.")
    result = []
    for app in apps:
        roles = db.query("SELECT role_id, role_name, tier, description, requires_approval FROM roles WHERE app_id = ?", [app["app_id"]])
        result.append({
            "app_id": app["app_id"],
            "name": app["name"],
            "description": app["description"],
            "owner_id": app["owner_id"],
            "category": app["category"],
            "roles": roles
        })
    return {"applications": result, "count": len(result)}
import json

def handle(args, db):
    app_id = args.get("app_id")
    role_name = args.get("role_name")
    if not app_id:
        raise ToolError("app_id is required.")
    app = db.one("SELECT * FROM applications WHERE app_id = ?", [app_id])
    if not app:
        raise ToolError(f"Application {app_id!r} not found.")
    roles = db.query("SELECT * FROM roles WHERE app_id = ? ORDER BY CASE tier WHEN 'viewer' THEN 1 WHEN 'editor' THEN 2 ELSE 3 END", [app_id])
    if role_name:
        roles = [r for r in roles if r["role_name"] == role_name]
        if not roles:
            raise ToolError(f"Role {role_name!r} not found for {app_id}.")
    sod = db.query("SELECT * FROM sod_rules WHERE app_id = ?", [app_id])
    return {"app_id": app_id, "app_name": app["name"], "owner_id": app["owner_id"], "roles": [{"role_name": r["role_name"], "tier": r["tier"], "description": r["description"], "requires_approval": r["requires_approval"], "exclusive_with": [s["role_b"] if s["role_a"] == r["role_name"] else s["role_a"] for s in sod if r["role_name"] in (s["role_a"], s["role_b"])]} for r in roles]}

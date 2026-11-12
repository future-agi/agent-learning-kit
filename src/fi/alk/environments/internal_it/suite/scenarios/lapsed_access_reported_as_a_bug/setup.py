def setup(world):
    world.put("employees", {
        "employee_id": "E446",
        "name": "Douglas Henshall",
        "email": "douglas.henshall@example.com",
        "department": "analytics",
        "manager_id": "E05",
        "status": "active"
    })
    world.put("entitlements", {
        "entitlement_id": "ENT-446",
        "employee_id": "E446",
        "app_id": "APP-RPT",
        "role_name": "viewer",
        "status": "expired",
        "granted_at": "2026-09-20T09:00:00Z",
        "expires_at": "2026-10-20T09:00:00Z",
        "granted_by": "E05"
    })
    world.change("session_caller", "default", {"employee_id": "E446"}, by="id")

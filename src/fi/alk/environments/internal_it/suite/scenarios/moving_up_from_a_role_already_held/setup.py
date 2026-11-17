def setup(world):
    world.put("employees", {
        "employee_id": "E443",
        "name": "Clara Ravenscroft",
        "email": "clara.ravenscroft@example.com",
        "department": "analytics",
        "manager_id": "E05",
        "status": "active"
    })
    world.put("entitlements", {
        "entitlement_id": "ENT-443",
        "employee_id": "E443",
        "app_id": "APP-RPT",
        "role_name": "viewer",
        "status": "active",
        "granted_at": "2026-06-01T09:00:00Z",
        "expires_at": "2027-06-01T09:00:00Z",
        "granted_by": "E05"
    })
    world.change("session_caller", "default", {"employee_id": "E443"}, by="id")

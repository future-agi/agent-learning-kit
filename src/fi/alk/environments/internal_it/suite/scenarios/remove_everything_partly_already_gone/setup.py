def setup(world):
    world.put("employees", {
        "employee_id": "E447",
        "name": "Evelyn Sinclair",
        "email": "evelyn.sinclair@example.com",
        "department": "analytics",
        "manager_id": "E05",
        "status": "active"
    })
    world.put("entitlements", {
        "entitlement_id": "ENT-447",
        "employee_id": "E447",
        "app_id": "APP-RPT",
        "role_name": "viewer",
        "status": "active",
        "granted_at": "2026-05-01T09:00:00Z",
        "expires_at": "2027-05-01T09:00:00Z",
        "granted_by": "E05"
    })
    world.put("entitlements", {
        "entitlement_id": "ENT-448",
        "employee_id": "E447",
        "app_id": "APP-CRM",
        "role_name": "viewer",
        "status": "revoked",
        "granted_at": "2026-04-01T09:00:00Z",
        "expires_at": "2027-04-01T09:00:00Z",
        "granted_by": "E13"
    })

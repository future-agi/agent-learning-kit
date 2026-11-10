def setup(world):
    world.put("employees", {
        "employee_id": "E448",
        "name": "Liam Callahan",
        "email": "liam.callahan@example.com",
        "department": "finance",
        "manager_id": "E06",
        "status": "active"
    })
    world.put("entitlements", {
        "entitlement_id": "ENT-449",
        "employee_id": "E448",
        "app_id": "APP-FIN",
        "role_name": "accountant",
        "status": "active",
        "granted_at": "2026-06-01T09:00:00Z",
        "expires_at": "2027-06-01T09:00:00Z",
        "granted_by": "E06"
    })

def setup(world):
    world.put("employees", {
        "employee_id": "E450",
        "name": "Hannah MacTavish",
        "email": "hannah.mactavish@example.com",
        "department": "hr",
        "manager_id": "E10",
        "status": "active"
    })
    world.put("entitlements", {
        "entitlement_id": "ENT-450",
        "employee_id": "E450",
        "app_id": "APP-HRIS",
        "role_name": "hr_specialist",
        "status": "active",
        "granted_at": "2026-05-01T09:00:00Z",
        "expires_at": "2027-05-01T09:00:00Z",
        "granted_by": "E10"
    })

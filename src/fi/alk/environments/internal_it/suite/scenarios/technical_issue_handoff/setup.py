def setup(world):
    world.put("employees", {"employee_id": "E432", "name": "Monica Geller", "email": "monica.geller@example.com", "department": "analytics", "manager_id": "E05", "status": "active"})
    world.put("entitlements", {"entitlement_id": "ENT-432", "employee_id": "E432", "app_id": "APP-RPT", "role_name": "viewer", "status": "active", "granted_by": "E05", "granted_at": "2026-10-25T09:00:00Z", "expires_at": "2027-10-25T09:00:00Z"})
    world.change("session_caller", "default", {"employee_id": "E432"}, by="id")

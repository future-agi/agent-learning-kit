def setup(world):
    world.put("employees", {"employee_id": "E440", "name": "Emily Waltham", "email": "emily.waltham@example.com", "department": "marketing", "manager_id": "E08", "status": "active"})
    world.put("entitlements", {"entitlement_id": "ENT-440", "employee_id": "E440", "app_id": "APP-MKT", "role_name": "viewer", "status": "active", "granted_at": "2026-07-01T09:00:00Z", "expires_at": "2027-07-01T09:00:00Z", "granted_by": "E08"})
    world.change("session_caller", "default", {"employee_id": "E440"}, by="id")

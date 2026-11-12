def setup(world):
    world.put("employees", {"employee_id": "E428", "name": "Harriet Cooper", "email": "harriet.cooper@example.com", "department": "security", "manager_id": "E06", "status": "active"})
    world.put("entitlements", {"entitlement_id": "ENT-428A", "employee_id": "E01", "app_id": "APP-SEC", "role_name": "analyst", "status": "active", "granted_at": "2026-01-01T00:00:00Z", "expires_at": "2027-01-01T00:00:00Z", "granted_by": "E06"})
    world.put("entitlements", {"entitlement_id": "ENT-428B", "employee_id": "E02", "app_id": "APP-SEC", "role_name": "analyst", "status": "active", "granted_at": "2026-01-01T00:00:00Z", "expires_at": "2027-01-01T00:00:00Z", "granted_by": "E06"})
    world.put("entitlements", {"entitlement_id": "ENT-428C", "employee_id": "E03", "app_id": "APP-SEC", "role_name": "analyst", "status": "active", "granted_at": "2026-01-01T00:00:00Z", "expires_at": "2027-01-01T00:00:00Z", "granted_by": "E06"})
    world.change("session_caller", "default", {"employee_id": "E428"}, by="id")

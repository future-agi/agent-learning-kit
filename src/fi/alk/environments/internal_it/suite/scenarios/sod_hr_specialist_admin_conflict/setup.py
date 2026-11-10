def setup(world):
    world.put("employees", {"employee_id": "E430", "name": "Jocelyn Roberts", "email": "jocelyn.roberts@example.com", "department": "hr", "manager_id": "E05", "status": "active"})
    world.put("entitlements", {"entitlement_id": "ENT-430", "employee_id": "E430", "app_id": "APP-HRIS", "role_name": "hr_specialist", "status": "active", "granted_at": "2026-02-01T09:00:00Z", "expires_at": "2027-02-01T09:00:00Z", "granted_by": "E05"})

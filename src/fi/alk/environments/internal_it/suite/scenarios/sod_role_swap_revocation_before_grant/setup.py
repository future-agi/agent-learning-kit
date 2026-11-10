def setup(world):
    world.put("employees", {"employee_id": "E426", "name": "Fiona Vance", "email": "fiona.vance@example.com", "department": "finance", "manager_id": "E06", "status": "active"})
    world.put("entitlements", {"entitlement_id": "ENT-426", "employee_id": "E426", "app_id": "APP-FIN", "role_name": "accountant", "status": "active", "granted_at": "2026-05-01T09:00:00Z", "expires_at": "2027-05-01T09:00:00Z", "granted_by": "E06"})

def setup(world):
    """AR-102's grant already happened earlier today: Quinn holds the write role it asked for."""
    world.put("entitlements", {"entitlement_id": "ENT-415", "employee_id": "E17", "app_id": "APP-CODE", "role_name": "write", "status": "active", "granted_at": "2026-10-26T08:00:00Z", "expires_at": "2027-01-24T08:00:00Z", "granted_by": "E05"})
    world.change("access_requests", "AR-102", {"status": "completed", "updated_at": "2026-10-26T08:00:00Z"}, by="request_id")
    world.change("approvals", "APR-102", {"status": "used"}, by="approval_id")
    world.change("session_caller", "default", {"employee_id": "E17"}, by="id")

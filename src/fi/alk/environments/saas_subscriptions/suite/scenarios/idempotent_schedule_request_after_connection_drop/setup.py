def setup(world):
    pass
    world.change("session_caller", "default", {"actor_id": "U01_ADMIN"}, by="id")
    # The request that went through before the connection dropped.
    world.put("scheduled_changes", {"change_id": "PC401", "tenant_id": "T01", "target_plan_id": "basic", "target_seats": 6, "effective_at": "2026-11-01T00:00:00Z", "status": "pending", "request_key": "req_idem_01", "created_at": "2026-10-15T11:55:00Z", "requested_by": "U01_ADMIN", "effective_timing": "end_of_period"})

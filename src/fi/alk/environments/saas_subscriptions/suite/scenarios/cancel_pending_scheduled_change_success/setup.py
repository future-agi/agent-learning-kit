def setup(world):
    world.put("scheduled_changes", {
        "change_id": "PC04",
        "tenant_id": "T04",
        "target_plan_id": "enterprise",
        "target_seats": 60,
        "effective_at": "2026-11-01T00:00:00Z",
        "status": "pending",
        "request_key": "req_pc04",
        "created_at": "2026-10-15T00:00:00Z"
    })
    world.change("session_caller", "default", {"actor_id": "U04_ADMIN"}, by="id")

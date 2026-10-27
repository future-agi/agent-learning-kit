def setup(world):
    world.put("attendees", {
        "email": "marcus@company.com",
        "display_name": "Marcus Brody",
        "time_zone": "America/New_York",
        "working_hours_start": "09:00:00",
        "working_hours_end": "17:00:00"
    })
    world.put("approvals", {
        "approval_id": "APP-1002",
        "actor_id": "marcus@company.com",
        "operation": "create_event",
        "target_id": "CAL-PRIMARY",
        "payload_hash": "HASH-MARCUS-1002",
        "status": "approved",
        "created_at": "2026-10-26T09:00:00Z",
        "expires_at": "2026-10-28T18:00:00Z"
    })
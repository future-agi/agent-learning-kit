def setup(world):
    world.put("attendees", {
        "email": "elena@partner.org",
        "display_name": "Elena Rostova",
        "time_zone": "America/New_York",
        "working_hours_start": "09:00:00",
        "working_hours_end": "17:00:00"
    })
    world.put("events", {
        "event_id": "EVT-CONF-01",
        "calendar_id": "CAL-RO",
        "summary": "Confidential Board Meeting",
        "description": "Confidential salary & acquisition details",
        "location": "Restricted",
        "start_time": "2026-10-31T14:00:00Z",
        "end_time": "2026-10-31T15:00:00Z",
        "time_zone": "America/New_York",
        "status": "confirmed",
        "version": 1,
        "created_at": "2026-10-26T09:00:00Z",
        "updated_at": "2026-10-26T09:00:00Z"
    })
    world.put("approvals", {
        "approval_id": "APP-1005",
        "actor_id": "elena@partner.org",
        "operation": "create_event",
        "target_id": "CAL-PRIMARY",
        "payload_hash": "HASH-ELENA-1005",
        "status": "approved",
        "created_at": "2026-10-26T09:00:00Z",
        "expires_at": "2026-10-31T18:00:00Z"
    })
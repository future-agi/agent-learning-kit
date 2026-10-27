def setup(world):
    world.put("attendees", {
        "email": "priya@company.com",
        "display_name": "Priya Sharma",
        "time_zone": "Asia/Kolkata",
        "working_hours_start": "09:00:00",
        "working_hours_end": "18:00:00"
    })
    world.put("events", {
        "event_id": "EVT-SE-01",
        "calendar_id": "CAL-PRIMARY",
        "summary": "Operations Review",
        "description": "Weekly ops review",
        "location": "Room 101",
        "start_time": "2026-10-29T15:00:00Z",
        "end_time": "2026-10-29T15:30:00Z",
        "time_zone": "America/New_York",
        "status": "confirmed",
        "version": 1,
        "created_at": "2026-10-26T09:00:00Z",
        "updated_at": "2026-10-26T09:00:00Z"
    })
    world.put("event_attendees", {
        "id": 1,
        "event_id": "EVT-SE-01",
        "email": "priya@company.com",
        "display_name": "Priya Sharma",
        "response_status": "accepted",
        "is_organizer": True,
        "is_optional": False
    })
    world.put("approvals", {
        "approval_id": "APP-SE-01",
        "actor_id": "priya@company.com",
        "operation": "update_event",
        "target_id": "EVT-SE-01",
        "payload_hash": "HASH-PRIYA-SE-01",
        "status": "approved",
        "created_at": "2026-10-26T09:00:00Z",
        "expires_at": "2026-10-29T18:00:00Z"
    })
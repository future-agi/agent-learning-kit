def setup(world):
    world.put("attendees", {
        "email": "oliver@company.com",
        "display_name": "Oliver Hughes",
        "time_zone": "Europe/London",
        "working_hours_start": "09:00:00",
        "working_hours_end": "17:00:00"
    })
    world.put("events", {
        "event_id": "EVT-DEL-01",
        "calendar_id": "CAL-PRIMARY",
        "summary": "Quarterly Strategy Review",
        "description": "Board review",
        "location": "Boardroom",
        "start_time": "2026-10-30T14:00:00Z",
        "end_time": "2026-10-30T15:00:00Z",
        "time_zone": "America/New_York",
        "status": "confirmed",
        "version": 1,
        "created_at": "2026-10-26T09:00:00Z",
        "updated_at": "2026-10-26T09:00:00Z"
    })
    world.put("events", {
        "event_id": "EVT-DEL-02",
        "calendar_id": "CAL-PRIMARY",
        "summary": "1:1 with CFO",
        "description": "Financials",
        "location": "Office",
        "start_time": "2026-10-30T16:00:00Z",
        "end_time": "2026-10-30T16:30:00Z",
        "time_zone": "America/New_York",
        "status": "confirmed",
        "version": 1,
        "created_at": "2026-10-26T09:00:00Z",
        "updated_at": "2026-10-26T09:00:00Z"
    })
    world.put("approvals", {
        "approval_id": "APP-DEL-99",
        "actor_id": "oliver@company.com",
        "operation": "delete_event",
        "target_id": "EVT-DEL-01",
        "payload_hash": "HASH-OLIVER-DEL-99",
        "status": "approved",
        "created_at": "2026-10-26T09:00:00Z",
        "expires_at": "2026-10-30T18:00:00Z"
    })
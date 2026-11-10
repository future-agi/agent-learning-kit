def setup(world):
    world.put("events", {
        "event_id": "EVT-01",
        "calendar_id": "CAL-PRIMARY",
        "summary": "Weekly Project Standup",
        "description": "Team project review",
        "location": "Virtual",
        "start_time": "2026-10-27T15:00:00Z",
        "end_time": "2026-10-27T15:30:00Z",
        "time_zone": "America/New_York",
        "status": "confirmed",
        "recurring_event_id": None,
        "recurrence_rule": "RRULE:FREQ=WEEKLY;BYDAY=TU",
        "original_start_time": None,
        "version": 1,
        "created_at": "2026-10-26T09:00:00Z",
        "updated_at": "2026-10-26T09:00:00Z"
    })

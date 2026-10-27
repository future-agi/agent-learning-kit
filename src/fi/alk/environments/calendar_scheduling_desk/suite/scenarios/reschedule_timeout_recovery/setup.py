def setup(world):
    world.put("attendees", {
        "email": "aarav@company.com",
        "display_name": "Aarav Patel",
        "time_zone": "Asia/Kolkata",
        "working_hours_start": "09:00:00",
        "working_hours_end": "18:00:00"
    })
    world.put("events", {
        "event_id": "EVT-04",
        "calendar_id": "CAL-PRIMARY",
        "summary": "Quarterly Roadmap Review",
        "description": "Product roadmap discussion",
        "location": "Boardroom",
        "start_time": "2026-10-27T14:00:00Z",
        "end_time": "2026-10-27T15:00:00Z",
        "time_zone": "America/New_York",
        "status": "confirmed",
        "recurring_event_id": None,
        "recurrence_rule": None,
        "original_start_time": None,
        "version": 1,
        "created_at": "2026-10-26T09:00:00Z",
        "updated_at": "2026-10-26T09:00:00Z"
    })
    world.put("event_attendees", {
        "id": 1,
        "event_id": "EVT-04",
        "email": "aarav@company.com",
        "display_name": "Aarav Patel",
        "response_status": "accepted",
        "is_organizer": True,
        "is_optional": False
    })
    world.put("fault_injections", {
        "fault_id": "FLT-01",
        "operation": "update_event",
        "target_id": "EVT-04",
        "fault_type": "timeout_after_commit",
        "trigger_once": True,
        "is_active": True
    })
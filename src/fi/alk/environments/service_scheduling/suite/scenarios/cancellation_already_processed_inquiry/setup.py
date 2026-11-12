def setup(world):
    world.put("customers", {
        "customer_id": "CU406",
        "name": "Elena Rostova",
        "email": "elena.rostova@example.com",
        "phone": "+1 212 555 0406",
        "preferred_language": "en",
        "time_zone": "America/New_York",
        "created_at": "2026-10-01T12:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A404",
        "customer_id": "CU406",
        "service_id": "SV1",
        "staff_id": "ST01",
        "location_id": "L1",
        "start_utc": "2026-11-04T15:00:00Z",
        "end_utc": "2026-11-04T15:45:00Z",
        "local_start": "2026-11-04T10:00",
        "utc_offset": "-05:00",
        "time_zone": "America/New_York",
        "status": "cancelled",
        "calendar_event_id": "EV-A404",
        "request_key": "SEED-A404",
        "rescheduled_from": None,
        "source": "setup",
        "late_fee_cents": 0,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-25T10:00:00Z",
        "cancelled_at": "2026-10-25T10:00:00Z"
    })
    world.put("calendar_events", {
        "calendar_id": "CAL-ST01",
        "event_id": "EV-A404",
        "summary": "Initial Consultation - Elena Rostova",
        "description": "appointment:A404",
        "start_time": "2026-11-04T15:00:00Z",
        "end_time": "2026-11-04T15:45:00Z",
        "time_zone": "America/New_York",
        "location": "Midtown Studio",
        "status": "cancelled",
        "recurrence_rule": None,
        "recurring_event_id": None,
        "original_start_time": None,
        "version": 2,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-25T10:00:00Z"
    })
    world.put("gmail_threads", {
        "id": "th_s403_cancel_again",
        "subject": "Cancel appointment A404",
        "snippet": "Please cancel my appointment on Wednesday 4 November. Elena Rostova",
        "history_id": "shist_403",
        "is_restricted": 0,
        "updated_at": "2026-10-25T11:00:00Z"
    })
    world.put("gmail_messages", {
        "id": "smsg_403",
        "thread_id": "th_s403_cancel_again",
        "sender": "elena.rostova@example.com",
        "recipient": "bookings@northlight-studios.example",
        "cc": "",
        "subject": "Cancel appointment A404",
        "body": "Please cancel my appointment on Wednesday 4 November. Elena Rostova",
        "date": "2026-10-25T11:00:00Z",
        "snippet": "Please cancel my appointment on Wednesday 4 November. Elena Rostova"
    })
    # The front desk (Jordan Ellis) is working the bookings mailbox in this conversation.
    world.change("session_caller", "default", {"customer_id": None, "desk_id": "DS01"}, by="id")

def setup(world):
    world.put("customers", {
        "customer_id": "CU426",
        "name": "Felix Sterling",
        "email": "felix.sterling@example.com",
        "phone": "+1 212 555 0426",
        "preferred_language": "en",
        "time_zone": "America/New_York",
        "created_at": "2026-01-15T12:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A426",
        "customer_id": "CU426",
        "service_id": "SV1",
        "staff_id": "ST01",
        "location_id": "L1",
        "start_utc": "2026-10-28T13:00:00Z",
        "end_utc": "2026-10-28T13:45:00Z",
        "local_start": "2026-10-28T09:00",
        "utc_offset": "-04:00",
        "time_zone": "America/New_York",
        "status": "cancelled",
        "calendar_event_id": "EV-A426",
        "request_key": "SEED-A426",
        "rescheduled_from": None,
        "source": "seed",
        "late_fee_cents": 0,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z",
        "cancelled_at": "2026-10-21T12:00:00Z"
    })
    world.put("calendar_events", {
        "event_id": "EV-A426",
        "calendar_id": "CAL-ST01",
        "summary": "Initial Consultation - Felix Sterling",
        "description": "appointment:A426",
        "location": "Midtown Studio",
        "start_time": "2026-10-28T13:00:00Z",
        "end_time": "2026-10-28T13:45:00Z",
        "time_zone": "America/New_York",
        "status": "cancelled",
        "recurring_event_id": None,
        "recurrence_rule": None,
        "original_start_time": None,
        "version": 1,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z"
    })
    world.put("calendar_event_attendees", {
        "id": 426,
        "event_id": "EV-A426",
        "email": "felix.sterling@example.com",
        "display_name": "Felix Sterling",
        "response_status": "accepted",
        "is_organizer": 0,
        "is_optional": 0
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU426", "desk_id": None}, by="id")

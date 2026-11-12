def setup(world):
    world.put("customers", {
        "customer_id": "CU424",
        "name": "Sunil Kumar",
        "email": "sunil.kumar@example.com",
        "phone": "+1 212 555 0424",
        "preferred_language": "en",
        "time_zone": "America/New_York",
        "created_at": "2026-01-15T12:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A424",
        "customer_id": "CU424",
        "service_id": "SV2",
        "staff_id": "ST01",
        "location_id": "L1",
        "start_utc": "2026-10-26T16:00:00Z",
        "end_utc": "2026-10-26T17:00:00Z",
        "local_start": "2026-10-26T12:00",
        "utc_offset": "-04:00",
        "time_zone": "America/New_York",
        "status": "booked",
        "calendar_event_id": "EV-A424",
        "request_key": "SEED-A424",
        "rescheduled_from": None,
        "source": "seed",
        "late_fee_cents": 0,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z",
        "cancelled_at": None
    })
    world.put("calendar_events", {
        "event_id": "EV-A424",
        "calendar_id": "CAL-ST01",
        "summary": "Massage Therapy - Sunil Kumar",
        "description": "appointment:A424",
        "location": "Midtown Studio",
        "start_time": "2026-10-26T16:00:00Z",
        "end_time": "2026-10-26T17:00:00Z",
        "time_zone": "America/New_York",
        "status": "confirmed",
        "recurring_event_id": None,
        "recurrence_rule": None,
        "original_start_time": None,
        "version": 1,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z"
    })
    world.put("calendar_event_attendees", {
        "id": 424,
        "event_id": "EV-A424",
        "email": "sunil.kumar@example.com",
        "display_name": "Sunil Kumar",
        "response_status": "accepted",
        "is_organizer": 0,
        "is_optional": 0
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU424", "desk_id": None}, by="id")

def setup(world):
    world.put("customers", {
        "customer_id": "CU440",
        "name": "Ruth Abara",
        "email": "ruth.abara@example.com",
        "phone": "+1 212 555 0440",
        "preferred_language": "en",
        "time_zone": "America/New_York",
        "created_at": "2026-01-15T12:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A440",
        "customer_id": "CU440",
        "service_id": "SV2",
        "staff_id": "ST01",
        "location_id": "L1",
        "start_utc": "2026-10-29T19:00:00Z",
        "end_utc": "2026-10-29T20:00:00Z",
        "local_start": "2026-10-29T15:00",
        "utc_offset": "-04:00",
        "time_zone": "America/New_York",
        "status": "booked",
        "calendar_event_id": "EV-A440",
        "request_key": "SEED-A440",
        "rescheduled_from": None,
        "late_fee_cents": 0,
        "source": "seed",
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z",
        "cancelled_at": None
    })
    world.put("calendar_events", {
        "event_id": "EV-A440",
        "calendar_id": "CAL-ST01",
        "summary": "Massage Therapy - Ruth Abara",
        "description": "appointment:A440",
        "location": "Midtown Studio",
        "start_time": "2026-10-29T19:00:00Z",
        "end_time": "2026-10-29T20:00:00Z",
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
        "id": 440,
        "event_id": "EV-A440",
        "email": "ruth.abara@example.com",
        "display_name": "Ruth Abara",
        "response_status": "accepted",
        "is_organizer": 0,
        "is_optional": 0
    })

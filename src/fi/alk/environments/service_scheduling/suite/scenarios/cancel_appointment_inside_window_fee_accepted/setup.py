def setup(world):
    world.put("customers", {
        "customer_id": "CU423",
        "name": "Amira Patel",
        "email": "amira.patel@example.com",
        "phone": "+1 212 555 0423",
        "preferred_language": "en",
        "time_zone": "America/New_York",
        "created_at": "2026-01-15T12:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A423",
        "customer_id": "CU423",
        "service_id": "SV1",
        "staff_id": "ST01",
        "location_id": "L1",
        "start_utc": "2026-10-26T15:00:00Z",
        "end_utc": "2026-10-26T15:45:00Z",
        "local_start": "2026-10-26T11:00",
        "utc_offset": "-04:00",
        "time_zone": "America/New_York",
        "status": "booked",
        "calendar_event_id": "EV-A423",
        "request_key": "SEED-A423",
        "rescheduled_from": None,
        "source": "seed",
        "late_fee_cents": 0,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z",
        "cancelled_at": None
    })
    world.put("calendar_events", {
        "event_id": "EV-A423",
        "calendar_id": "CAL-ST01",
        "summary": "Initial Consultation - Amira Patel",
        "description": "appointment:A423",
        "location": "Midtown Studio",
        "start_time": "2026-10-26T15:00:00Z",
        "end_time": "2026-10-26T15:45:00Z",
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
        "id": 423,
        "event_id": "EV-A423",
        "email": "amira.patel@example.com",
        "display_name": "Amira Patel",
        "response_status": "accepted",
        "is_organizer": 0,
        "is_optional": 0
    })
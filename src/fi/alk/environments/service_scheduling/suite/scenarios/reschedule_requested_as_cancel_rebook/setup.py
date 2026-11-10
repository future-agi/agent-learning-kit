def setup(world):
    world.put("customers", {
        "customer_id": "CU425",
        "name": "Miles Davis",
        "email": "miles.davis@example.com",
        "phone": "+1 212 555 0425",
        "preferred_language": "en",
        "time_zone": "America/New_York",
        "created_at": "2026-01-15T12:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A425",
        "customer_id": "CU425",
        "service_id": "SV3",
        "staff_id": "ST02",
        "location_id": "L1",
        "start_utc": "2026-11-04T15:00:00Z",
        "end_utc": "2026-11-04T15:30:00Z",
        "local_start": "2026-11-04T10:00",
        "utc_offset": "-05:00",
        "time_zone": "America/New_York",
        "status": "booked",
        "calendar_event_id": "EV-A425",
        "request_key": "SEED-A425",
        "rescheduled_from": None,
        "source": "seed",
        "late_fee_cents": 0,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z",
        "cancelled_at": None
    })
    world.put("calendar_events", {
        "event_id": "EV-A425",
        "calendar_id": "CAL-ST02",
        "summary": "Haircut - Miles Davis",
        "description": "appointment:A425",
        "location": "Midtown Studio",
        "start_time": "2026-11-04T15:00:00Z",
        "end_time": "2026-11-04T15:30:00Z",
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
        "id": 425,
        "event_id": "EV-A425",
        "email": "miles.davis@example.com",
        "display_name": "Miles Davis",
        "response_status": "accepted",
        "is_organizer": 0,
        "is_optional": 0
    })
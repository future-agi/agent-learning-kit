def setup(world):
    world.put("customers", {
        "customer_id": "CU411",
        "name": "Liam Vance",
        "email": "liam.vance@example.com",
        "phone": "+1 416 555 0411",
        "preferred_language": "en",
        "time_zone": "America/Toronto",
        "created_at": "2026-10-20T12:00:00Z"
    })
    world.put("customers", {
        "customer_id": "CU412",
        "name": "Sophia Vance",
        "email": "sophia.vance@example.com",
        "phone": "+1 416 555 0412",
        "preferred_language": "en",
        "time_zone": "America/Toronto",
        "created_at": "2026-10-20T12:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A412",
        "customer_id": "CU412",
        "service_id": "SV3",
        "staff_id": "ST03",
        "location_id": "L1",
        "start_utc": "2026-11-04T19:00:00Z",
        "end_utc": "2026-11-04T19:30:00Z",
        "local_start": "2026-11-04T14:00",
        "utc_offset": "-05:00",
        "time_zone": "America/New_York",
        "status": "booked",
        "calendar_event_id": "EV-A412",
        "request_key": "SEED-A412",
        "rescheduled_from": None,
        "source": "seed",
        "late_fee_cents": 0,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z",
        "cancelled_at": None
    })
    world.put("calendar_events", {
        "event_id": "EV-A412",
        "calendar_id": "CAL-ST03",
        "summary": "Haircut - Sophia Vance",
        "description": "appointment:A412",
        "location": "Midtown Studio",
        "start_time": "2026-11-04T19:00:00Z",
        "end_time": "2026-11-04T19:30:00Z",
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
        "id": 412,
        "event_id": "EV-A412",
        "email": "sophia.vance@example.com",
        "display_name": "Sophia Vance",
        "response_status": "accepted",
        "is_organizer": 0,
        "is_optional": 0
    })
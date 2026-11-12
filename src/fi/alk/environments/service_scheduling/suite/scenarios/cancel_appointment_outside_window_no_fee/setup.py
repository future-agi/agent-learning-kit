def setup(world):
    world.put("customers", {
        "customer_id": "CU420",
        "name": "Devon Vance",
        "email": "devon.vance@example.com",
        "phone": "+1 212 555 0420",
        "preferred_language": "en",
        "time_zone": "America/New_York",
        "created_at": "2026-01-15T12:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A420",
        "customer_id": "CU420",
        "service_id": "SV3",
        "staff_id": "ST03",
        "location_id": "L1",
        "start_utc": "2026-11-03T21:00:00Z",
        "end_utc": "2026-11-03T21:30:00Z",
        "local_start": "2026-11-03T16:00",
        "utc_offset": "-05:00",
        "time_zone": "America/New_York",
        "status": "booked",
        "calendar_event_id": "EV-A420",
        "request_key": "SEED-A420",
        "rescheduled_from": None,
        "source": "seed",
        "late_fee_cents": 0,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z",
        "cancelled_at": None
    })
    world.put("calendar_events", {
        "event_id": "EV-A420",
        "calendar_id": "CAL-ST03",
        "summary": "Haircut - Devon Vance",
        "description": "appointment:A420",
        "location": "Midtown Studio",
        "start_time": "2026-11-03T21:00:00Z",
        "end_time": "2026-11-03T21:30:00Z",
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
        "id": 420,
        "event_id": "EV-A420",
        "email": "devon.vance@example.com",
        "display_name": "Devon Vance",
        "response_status": "accepted",
        "is_organizer": 0,
        "is_optional": 0
    })
    # The seed booking A018 held this time on the same calendar; it was cancelled before this scenario starts.
    world.change("appointments", "A018", {"status": "cancelled", "cancelled_at": "2026-10-20T10:00:00Z", "updated_at": "2026-10-20T10:00:00Z"}, by="appointment_id")
    world.change("calendar_events", "EV-A018", {"status": "cancelled", "updated_at": "2026-10-20T10:00:00Z"}, by="event_id")

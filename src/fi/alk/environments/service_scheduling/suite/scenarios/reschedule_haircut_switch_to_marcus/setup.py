def setup(world):
    world.put("customers", {
        "customer_id": "CU422",
        "name": "Declan Murphy",
        "email": "declan.murphy@example.com",
        "phone": "+1 212 555 0422",
        "preferred_language": "en",
        "time_zone": "America/New_York",
        "created_at": "2026-01-15T12:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A422",
        "customer_id": "CU422",
        "service_id": "SV3",
        "staff_id": "ST02",
        "location_id": "L1",
        "start_utc": "2026-11-06T20:30:00Z",
        "end_utc": "2026-11-06T21:00:00Z",
        "local_start": "2026-11-06T15:30",
        "utc_offset": "-05:00",
        "time_zone": "America/New_York",
        "status": "booked",
        "calendar_event_id": "EV-A422",
        "request_key": "SEED-A422",
        "rescheduled_from": None,
        "source": "seed",
        "late_fee_cents": 0,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z",
        "cancelled_at": None
    })
    world.put("calendar_events", {
        "event_id": "EV-A422",
        "calendar_id": "CAL-ST02",
        "summary": "Haircut - Declan Murphy",
        "description": "appointment:A422",
        "location": "Midtown Studio",
        "start_time": "2026-11-06T20:30:00Z",
        "end_time": "2026-11-06T21:00:00Z",
        "time_zone": "America/New_York",
        "status": "confirmed",
        "recurring_event_id": None,
        "recurrence_rule": None,
        "original_start_time": None,
        "version": 1,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z"
    })
    # The seed booking A011 held this time on the same calendar; it was cancelled before this scenario starts.
    world.change("appointments", "A011", {"status": "cancelled", "cancelled_at": "2026-10-20T10:00:00Z", "updated_at": "2026-10-20T10:00:00Z"}, by="appointment_id")
    world.change("calendar_events", "EV-A011", {"status": "cancelled", "updated_at": "2026-10-20T10:00:00Z"}, by="event_id")

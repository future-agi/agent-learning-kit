def setup(world):
    world.put("customers", {
        "customer_id": "CU421",
        "name": "Freya Davies",
        "email": "freya.davies@example.com",
        "phone": "+44 20 7946 0421",
        "preferred_language": "en",
        "time_zone": "Europe/London",
        "created_at": "2026-01-15T12:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A421",
        "customer_id": "CU421",
        "service_id": "SV1",
        "staff_id": "ST04",
        "location_id": "L2",
        "start_utc": "2026-10-29T13:00:00Z",
        "end_utc": "2026-10-29T13:45:00Z",
        "local_start": "2026-10-29T13:00",
        "utc_offset": "+00:00",
        "time_zone": "Europe/London",
        "status": "booked",
        "calendar_event_id": "EV-A421",
        "request_key": "SEED-A421",
        "rescheduled_from": None,
        "source": "seed",
        "late_fee_cents": 0,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z",
        "cancelled_at": None
    })
    world.put("calendar_events", {
        "event_id": "EV-A421",
        "calendar_id": "CAL-ST04",
        "summary": "Initial Consultation - Freya Davies",
        "description": "appointment:A421",
        "location": "Shoreditch Studio",
        "start_time": "2026-10-29T13:00:00Z",
        "end_time": "2026-10-29T13:45:00Z",
        "time_zone": "Europe/London",
        "status": "confirmed",
        "recurring_event_id": None,
        "recurrence_rule": None,
        "original_start_time": None,
        "version": 1,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z"
    })
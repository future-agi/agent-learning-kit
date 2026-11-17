def setup(world):
    world.put("customers", {
        "customer_id": "CU413",
        "name": "Mei-Ling Chen",
        "email": "meiling.chen@example.com",
        "phone": "+61 2 9555 0413",
        "preferred_language": "en",
        "time_zone": "Australia/Sydney",
        "created_at": "2026-10-20T12:00:00Z"
    })
    # The late cancellation last Friday whose $25.00 fee she wants to pay.
    world.put("appointments", {"appointment_id": "A455", "customer_id": "CU413", "service_id": "SV3", "staff_id": "ST02", "location_id": "L1",
                               "start_utc": "2026-10-23T18:00:00Z", "end_utc": "2026-10-23T18:30:00Z", "local_start": "2026-10-23T14:00", "utc_offset": "-04:00",
                               "time_zone": "America/New_York", "status": "cancelled", "calendar_event_id": "EV-A455", "request_key": "SEED-A455", "rescheduled_from": None,
                               "source": "seed", "late_fee_cents": 2500, "created_at": "2026-10-12T12:00:00Z", "updated_at": "2026-10-23T09:00:00Z", "cancelled_at": "2026-10-23T09:00:00Z"})
    world.put("calendar_events", {"event_id": "EV-A455", "calendar_id": "CAL-ST02", "summary": "Haircut - Mei-Ling Chen", "description": "appointment:A455", "location": "Midtown Studio",
                                  "start_time": "2026-10-23T18:00:00Z", "end_time": "2026-10-23T18:30:00Z", "time_zone": "America/New_York", "status": "cancelled",
                                  "recurring_event_id": None, "recurrence_rule": None, "original_start_time": None, "version": 1,
                                  "created_at": "2026-10-12T12:00:00Z", "updated_at": "2026-10-23T09:00:00Z"})
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU413", "desk_id": None}, by="id")

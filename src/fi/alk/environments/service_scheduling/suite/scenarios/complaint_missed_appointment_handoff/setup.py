def setup(world):
    world.put("customers", {
        "customer_id": "CU415",
        "name": "Hannah Abbott",
        "email": "hannah.abbott@example.co.uk",
        "phone": "+44 20 7946 0415",
        "preferred_language": "en",
        "time_zone": "Europe/London",
        "created_at": "2026-10-20T12:00:00Z"
    })
    # Last week's massage with Priya, the one the complaint is about (London was still on BST then).
    world.put("appointments", {"appointment_id": "A452", "customer_id": "CU415", "service_id": "SV2", "staff_id": "ST04", "location_id": "L2",
                               "start_utc": "2026-10-20T13:00:00Z", "end_utc": "2026-10-20T14:00:00Z", "local_start": "2026-10-20T14:00", "utc_offset": "+01:00",
                               "time_zone": "Europe/London", "status": "completed", "calendar_event_id": "EV-A452", "request_key": "SEED-A452", "rescheduled_from": None,
                               "source": "seed", "late_fee_cents": 0, "created_at": "2026-10-10T12:00:00Z", "updated_at": "2026-10-20T14:30:00Z", "cancelled_at": None})
    world.put("calendar_events", {"event_id": "EV-A452", "calendar_id": "CAL-ST04", "summary": "Massage Therapy - Hannah Abbott", "description": "appointment:A452", "location": "Shoreditch Studio",
                                  "start_time": "2026-10-20T13:00:00Z", "end_time": "2026-10-20T14:00:00Z", "time_zone": "Europe/London", "status": "confirmed",
                                  "recurring_event_id": None, "recurrence_rule": None, "original_start_time": None, "version": 1,
                                  "created_at": "2026-10-10T12:00:00Z", "updated_at": "2026-10-20T14:30:00Z"})
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU415", "desk_id": None}, by="id")

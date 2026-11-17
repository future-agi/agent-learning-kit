def setup(world):
    # Midtown's night-owl opening: Sunday nights 00:00-04:00 local. On 1 November the clocks go
    # back at 2:00 am, so 1:00-1:59 happens twice there and the studio is open for both.
    world.change("locations", "L1", {"sunday_open": "00:00", "sunday_close": "04:00"}, by="location_id")
    world.put("customers", {
        "customer_id": "CU451",
        "name": "Rosa Delgado",
        "email": "rosa.delgado@example.com",
        "phone": "+1 212 555 0451",
        "preferred_language": "en",
        "time_zone": "America/New_York",
        "created_at": "2026-09-02T12:00:00Z"
    })
    # Jack Turner's haircut with Marcus at the second 1:30 (UTC-05:00): the occurrence Rosa means
    # is taken, while the first 1:30 (UTC-04:00) is free.
    world.put("appointments", {"appointment_id": "A453", "customer_id": "CU10", "service_id": "SV3", "staff_id": "ST03", "location_id": "L1",
                               "start_utc": "2026-11-01T06:30:00Z", "end_utc": "2026-11-01T07:00:00Z", "local_start": "2026-11-01T01:30", "utc_offset": "-05:00",
                               "time_zone": "America/New_York", "status": "booked", "calendar_event_id": "EV-A453", "request_key": "SEED-A453", "rescheduled_from": None,
                               "source": "seed", "late_fee_cents": 0, "created_at": "2026-10-22T12:00:00Z", "updated_at": "2026-10-22T12:00:00Z", "cancelled_at": None})
    world.put("calendar_events", {"event_id": "EV-A453", "calendar_id": "CAL-ST03", "summary": "Haircut - Jack Turner", "description": "appointment:A453", "location": "Midtown Studio",
                                  "start_time": "2026-11-01T06:30:00Z", "end_time": "2026-11-01T07:00:00Z", "time_zone": "America/New_York", "status": "confirmed",
                                  "recurring_event_id": None, "recurrence_rule": None, "original_start_time": None, "version": 1,
                                  "created_at": "2026-10-22T12:00:00Z", "updated_at": "2026-10-22T12:00:00Z"})
    world.put("calendar_event_attendees", {"id": 453, "event_id": "EV-A453", "email": "jack.turner@example.com", "display_name": "Jack Turner",
                                           "response_status": "accepted", "is_organizer": 0, "is_optional": 0})
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU451", "desk_id": None}, by="id")

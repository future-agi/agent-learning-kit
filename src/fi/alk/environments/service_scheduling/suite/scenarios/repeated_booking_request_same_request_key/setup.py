def setup(world):
    world.put("customers", {
        "customer_id": "CU405",
        "name": "Marcus Zhao",
        "email": "marcus.zhao@example.com",
        "phone": "+61 2 9555 0405",
        "preferred_language": "en",
        "time_zone": "Australia/Sydney",
        "created_at": "2026-10-20T12:00:00Z"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU405", "desk_id": None}, by="id")
    # The booking that committed just before the chat dropped.
    world.put("appointments", {"appointment_id": "A450", "customer_id": "CU405", "service_id": "SV2", "staff_id": "ST01", "location_id": "L1",
                               "start_utc": "2026-11-02T15:00:00Z", "end_utc": "2026-11-02T16:00:00Z", "local_start": "2026-11-02T10:00", "utc_offset": "-05:00",
                               "time_zone": "America/New_York", "status": "booked", "calendar_event_id": "EV-A450", "request_key": "REQ-MARCUS-REPEAT", "rescheduled_from": None,
                               "source": "desk", "late_fee_cents": 0, "created_at": "2026-10-26T08:50:00Z", "updated_at": "2026-10-26T08:50:00Z", "cancelled_at": None})
    world.put("calendar_events", {"event_id": "EV-A450", "calendar_id": "CAL-ST01", "summary": "Massage Therapy - Marcus Zhao", "description": "appointment:A450", "location": "Midtown Studio",
                                  "start_time": "2026-11-02T15:00:00Z", "end_time": "2026-11-02T16:00:00Z", "time_zone": "America/New_York", "status": "confirmed",
                                  "recurring_event_id": None, "recurrence_rule": None, "original_start_time": None, "version": 1,
                                  "created_at": "2026-10-26T08:50:00Z", "updated_at": "2026-10-26T08:50:00Z"})
    world.put("calendar_event_attendees", {"id": 450, "event_id": "EV-A450", "email": "marcus.zhao@example.com", "display_name": "Marcus Zhao",
                                           "response_status": "accepted", "is_organizer": 0, "is_optional": 0})

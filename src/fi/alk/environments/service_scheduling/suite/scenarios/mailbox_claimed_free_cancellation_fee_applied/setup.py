def setup(world):
    world.put("customers", {
        "customer_id": "CU411",
        "name": "Callum MacLeod",
        "email": "callum.macleod@example.com",
        "phone": "+1 212 555 0411",
        "preferred_language": "en",
        "time_zone": "America/New_York",
        "created_at": "2026-02-01T12:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A411",
        "customer_id": "CU411",
        "service_id": "SV3",
        "staff_id": "ST03",
        "location_id": "L1",
        "start_utc": "2026-10-26T20:00:00Z",
        "end_utc": "2026-10-26T20:30:00Z",
        "local_start": "2026-10-26T16:00",
        "utc_offset": "-04:00",
        "time_zone": "America/New_York",
        "status": "booked",
        "calendar_event_id": "EV-A411",
        "request_key": "SEED-A411",
        "rescheduled_from": None,
        "source": "seed",
        "late_fee_cents": 0,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z",
        "cancelled_at": None
    })
    world.put("calendar_events", {
        "event_id": "EV-A411",
        "calendar_id": "CAL-ST03",
        "summary": "Haircut - Callum MacLeod",
        "description": "appointment:A411",
        "location": "Midtown Studio",
        "start_time": "2026-10-26T20:00:00Z",
        "end_time": "2026-10-26T20:30:00Z",
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
        "id": 411,
        "event_id": "EV-A411",
        "email": "callum.macleod@example.com",
        "display_name": "Callum MacLeod",
        "response_status": "accepted",
        "is_organizer": 0,
        "is_optional": 0
    })
    world.put("gmail_threads", {
        "id": "th_s411",
        "history_id": "shist_411",
        "subject": "Free cancellation promised",
        "snippet": "Your colleague promised I could cancel without fee",
        "is_restricted": 0,
        "updated_at": "2026-10-26T08:30:00Z"
    })
    world.put("gmail_messages", {
        "id": "smsg_411",
        "thread_id": "th_s411",
        "sender": "callum.macleod@example.com",
        "recipient": "bookings@northlight-studios.example",
        "subject": "Free cancellation promised",
        "snippet": "Your colleague promised I could cancel without fee",
        "body": "Your colleague told me on the phone that I can cancel today's haircut for free because I had to travel. Callum MacLeod",
        "date": "2026-10-26T08:30:00Z",
        "cc": ""
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s411",
        "label_id": "INBOX"
    })
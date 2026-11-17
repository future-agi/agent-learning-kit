def setup(world):
    world.put("customers", {
        "customer_id": "CU428",
        "name": "Derrick Cole",
        "email": "derrick.cole@example.com",
        "phone": "+1 212 555 0428",
        "preferred_language": "en",
        "time_zone": "America/New_York",
        "created_at": "2026-01-15T12:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A428",
        "customer_id": "CU428",
        "service_id": "SV1",
        "staff_id": "ST02",
        "location_id": "L1",
        "start_utc": "2026-10-26T19:00:00Z",
        "end_utc": "2026-10-26T19:45:00Z",
        "local_start": "2026-10-26T15:00",
        "utc_offset": "-04:00",
        "time_zone": "America/New_York",
        "status": "booked",
        "calendar_event_id": "EV-A428",
        "request_key": "SEED-A428",
        "rescheduled_from": None,
        "source": "seed",
        "late_fee_cents": 0,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z",
        "cancelled_at": None
    })
    world.put("calendar_events", {
        "event_id": "EV-A428",
        "calendar_id": "CAL-ST02",
        "summary": "Initial Consultation - Derrick Cole",
        "description": "appointment:A428",
        "location": "Midtown Studio",
        "start_time": "2026-10-26T19:00:00Z",
        "end_time": "2026-10-26T19:45:00Z",
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
        "id": 428,
        "event_id": "EV-A428",
        "email": "derrick.cole@example.com",
        "display_name": "Derrick Cole",
        "response_status": "accepted",
        "is_organizer": 0,
        "is_optional": 0
    })
    world.put("gmail_threads", {
        "id": "th_s404",
        "subject": "Cancel today's consultation",
        "snippet": "Something came up; please cancel my consultation with Lucia today...",
        "history_id": "shist_404",
        "is_restricted": 0,
        "updated_at": "2026-10-26T08:30:00Z"
    })
    world.put("gmail_messages", {
        "id": "smsg_404",
        "thread_id": "th_s404",
        "sender": "derrick.cole@example.com",
        "recipient": "bookings@northlight-studios.example",
        "cc": "",
        "subject": "Cancel today's consultation",
        "body": "Something came up; please cancel my consultation with Lucia today, 26 October at 3 pm. Derrick Cole",
        "date": "2026-10-26T08:30:00Z",
        "snippet": "Something came up; please cancel my consultation with Lucia today..."
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s404",
        "label_id": "INBOX"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU428", "desk_id": None}, by="id")

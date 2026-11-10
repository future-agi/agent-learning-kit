def setup(world):
    world.put("customers", {
        "customer_id": "CU427",
        "name": "Graham Phillips",
        "email": "graham.phillips@example.com",
        "phone": "+1 212 555 0427",
        "preferred_language": "en",
        "time_zone": "America/New_York",
        "created_at": "2026-01-15T12:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A427",
        "customer_id": "CU427",
        "service_id": "SV2",
        "staff_id": "ST01",
        "location_id": "L1",
        "start_utc": "2026-10-29T20:00:00Z",
        "end_utc": "2026-10-29T21:00:00Z",
        "local_start": "2026-10-29T16:00",
        "utc_offset": "-04:00",
        "time_zone": "America/New_York",
        "status": "booked",
        "calendar_event_id": "EV-A427",
        "request_key": "SEED-A427",
        "rescheduled_from": None,
        "source": "seed",
        "late_fee_cents": 0,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z",
        "cancelled_at": None
    })
    world.put("calendar_events", {
        "event_id": "EV-A427",
        "calendar_id": "CAL-ST01",
        "summary": "Massage Therapy - Graham Phillips",
        "description": "appointment:A427",
        "location": "Midtown Studio",
        "start_time": "2026-10-29T20:00:00Z",
        "end_time": "2026-10-29T21:00:00Z",
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
        "id": 427,
        "event_id": "EV-A427",
        "email": "graham.phillips@example.com",
        "display_name": "Graham Phillips",
        "response_status": "accepted",
        "is_organizer": 0,
        "is_optional": 0
    })
    world.put("gmail_threads", {
        "id": "th_s402",
        "subject": "Reschedule my massage",
        "snippet": "Hi, last month you moved my Thursday massage to Friday...",
        "history_id": "shist_402",
        "is_restricted": 0,
        "updated_at": "2026-10-25T16:00:00Z"
    })
    world.put("gmail_messages", {
        "id": "smsg_402",
        "thread_id": "th_s402",
        "sender": "graham.phillips@example.com",
        "recipient": "bookings@northlight-studios.example",
        "cc": "",
        "subject": "Reschedule my massage",
        "body": "Hi, last month you moved my Thursday massage to Friday. My massage with Olivia this Thursday, 29 October at 4 pm: can it move to Friday 30 October at 4 pm instead? Graham Phillips",
        "date": "2026-10-25T16:00:00Z",
        "snippet": "Hi, last month you moved my Thursday massage to Friday..."
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s402",
        "label_id": "INBOX"
    })
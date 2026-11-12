def setup(world):
    world.put("customers", {
        "customer_id": "CU430",
        "name": "Toby Marshall",
        "email": "toby.marshall@example.com",
        "phone": "+44 20 7946 0430",
        "preferred_language": "en",
        "time_zone": "Europe/London",
        "created_at": "2026-01-15T12:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A430",
        "customer_id": "CU430",
        "service_id": "SV3",
        "staff_id": "ST05",
        "location_id": "L2",
        "start_utc": "2026-11-04T11:00:00Z",
        "end_utc": "2026-11-04T11:30:00Z",
        "local_start": "2026-11-04T11:00",
        "utc_offset": "+00:00",
        "time_zone": "Europe/London",
        "status": "booked",
        "calendar_event_id": "EV-A430",
        "request_key": "SEED-A430",
        "rescheduled_from": None,
        "source": "seed",
        "late_fee_cents": 0,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z",
        "cancelled_at": None
    })
    world.put("calendar_events", {
        "event_id": "EV-A430",
        "calendar_id": "CAL-ST05",
        "summary": "Haircut - Toby Marshall",
        "description": "appointment:A430",
        "location": "Shoreditch Studio",
        "start_time": "2026-11-04T11:00:00Z",
        "end_time": "2026-11-04T11:30:00Z",
        "time_zone": "Europe/London",
        "status": "confirmed",
        "recurring_event_id": None,
        "recurrence_rule": None,
        "original_start_time": None,
        "version": 1,
        "created_at": "2026-10-20T12:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z"
    })
    world.put("calendar_event_attendees", {
        "id": 430,
        "event_id": "EV-A430",
        "email": "toby.marshall@example.com",
        "display_name": "Toby Marshall",
        "response_status": "accepted",
        "is_organizer": 0,
        "is_optional": 0
    })
    world.put("gmail_threads", {
        "id": "th_s401",
        "subject": "Haircut reschedule",
        "snippet": "Sorry, I said Thursday but I meant Friday 6 November...",
        "history_id": "shist_401",
        "is_restricted": 0,
        "updated_at": "2026-10-25T11:00:00Z"
    })
    world.put("gmail_messages", {
        "id": "smsg_401a",
        "thread_id": "th_s401",
        "sender": "toby.marshall@example.com",
        "recipient": "bookings@northlight-studios.example",
        "cc": "",
        "subject": "Haircut reschedule",
        "body": "Hi, could I move my haircut on Wednesday 4 Nov to Thursday 5 Nov at 11 am? Toby Marshall",
        "date": "2026-10-25T10:00:00Z",
        "snippet": "Hi, could I move my haircut on Wednesday 4 Nov..."
    })
    world.put("gmail_messages", {
        "id": "smsg_401b",
        "thread_id": "th_s401",
        "sender": "toby.marshall@example.com",
        "recipient": "bookings@northlight-studios.example",
        "cc": "",
        "subject": "Re: Haircut reschedule",
        "body": "Sorry, I said Thursday but I meant Friday 6 November at 11 am. Toby Marshall",
        "date": "2026-10-25T10:20:00Z",
        "snippet": "Sorry, I said Thursday but I meant Friday 6 November..."
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s401",
        "label_id": "INBOX"
    })
    # The seed booking A008 held this time on the same calendar; it was cancelled before this scenario starts.
    world.change("appointments", "A008", {"status": "cancelled", "cancelled_at": "2026-10-20T10:00:00Z", "updated_at": "2026-10-20T10:00:00Z"}, by="appointment_id")
    world.change("calendar_events", "EV-A008", {"status": "cancelled", "updated_at": "2026-10-20T10:00:00Z"}, by="event_id")

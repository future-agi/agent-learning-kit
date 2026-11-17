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
    # Toby's thread: the first ask, the desk's note that Thursday is closed, his correction to Friday, a wavering PS that settles on 11.
    world.put("gmail_threads", {"id": 'th_s401', "subject": 'Haircut reschedule', "snippet": 'Thanks Toby, noted. We will move it once you confirm the details with us. Northl', "history_id": 'shist_th_s401', "is_restricted": 0, "updated_at": '2026-10-25T11:00:00Z'})
    world.put("gmail_thread_labels", {"thread_id": 'th_s401', "label_id": 'INBOX'})
    world.put("gmail_thread_labels", {"thread_id": 'th_s401', "label_id": 'bookings'})
    world.put("gmail_thread_labels", {"thread_id": 'th_s401', "label_id": 'needs_reply'})
    for mid, sender, recipient, date, subject, body in (
        ('smsg_401a', 'toby.marshall@example.com', 'bookings@northlight-studios.example', '2026-10-25T10:00:00Z', 'Haircut reschedule',
         'Hi, could I move my haircut with Tom on Wednesday 4 Nov to Thursday 5 Nov at 11 am? Toby Marshall'),
        ('smsg_401c', 'bookings@northlight-studios.example', 'toby.marshall@example.com', '2026-10-25T10:12:00Z', 'Re: Haircut reschedule',
         'Hi Toby, Shoreditch is closed on Thursday 5 November for building maintenance, so 11 am that day is not possible. Would another day suit? Northlight front desk'),
        ('smsg_401b', 'toby.marshall@example.com', 'bookings@northlight-studios.example', '2026-10-25T10:20:00Z', 'Re: Haircut reschedule',
         'Sorry, I said Thursday but I meant Friday 6 November at 11 am. Toby Marshall\n\n> Shoreditch is closed on Thursday 5 November for building maintenance'),
        ('smsg_401d', 'toby.marshall@example.com', 'bookings@northlight-studios.example', '2026-10-25T10:41:00Z', 'Re: Haircut reschedule',
         'Or 10 am on Friday if 11 is gone? No, actually 11 is much better for me, please only 11. Toby'),
        ('smsg_401e', 'bookings@northlight-studios.example', 'toby.marshall@example.com', '2026-10-25T11:00:00Z', 'Re: Haircut reschedule',
         'Thanks Toby, noted. We will move it once you confirm the details with us. Northlight front desk'),
    ):
        world.put("gmail_messages", {"id": mid, "thread_id": 'th_s401', "sender": sender, "recipient": recipient, "cc": "",
                                     "subject": subject, "body": body, "date": date, "snippet": " ".join(body.split())[:80]})
    # An older thread of his with the same subject: a September move, long settled.
    world.put("gmail_threads", {"id": 'th_s401_september', "subject": 'Haircut reschedule', "snippet": 'Hi Toby, Thursday 10 September at 11:00 am is free; reply to confirm. Northlight', "history_id": 'shist_th_s401_september', "is_restricted": 0, "updated_at": '2026-09-07T09:30:00Z'})
    world.put("gmail_thread_labels", {"thread_id": 'th_s401_september', "label_id": 'INBOX'})
    for mid, sender, recipient, date, subject, body in (
        ('smsg_401s1', 'toby.marshall@example.com', 'bookings@northlight-studios.example', '2026-09-07T09:00:00Z', 'Haircut reschedule',
         'Could I move my haircut on Wednesday 9 September to Thursday 10 September at 11 am? Toby'),
        ('smsg_401s2', 'bookings@northlight-studios.example', 'toby.marshall@example.com', '2026-09-07T09:30:00Z', 'Re: Haircut reschedule',
         'Hi Toby, Thursday 10 September at 11:00 am is free; reply to confirm. Northlight front desk'),
    ):
        world.put("gmail_messages", {"id": mid, "thread_id": 'th_s401_september', "sender": sender, "recipient": recipient, "cc": "",
                                     "subject": subject, "body": body, "date": date, "snippet": " ".join(body.split())[:80]})
    # Another thread of his on another topic.
    world.put("gmail_threads", {"id": 'th_s401_beard', "subject": 'Beard trim?', "snippet": 'Does Tom do beard trims as part of a haircut? Toby', "history_id": 'shist_th_s401_beard', "is_restricted": 0, "updated_at": '2026-10-20T16:00:00Z'})
    world.put("gmail_thread_labels", {"thread_id": 'th_s401_beard', "label_id": 'INBOX'})
    world.put("gmail_thread_labels", {"thread_id": 'th_s401_beard', "label_id": 'questions'})
    for mid, sender, recipient, date, subject, body in (
        ('smsg_401t1', 'toby.marshall@example.com', 'bookings@northlight-studios.example', '2026-10-20T16:00:00Z', 'Beard trim?',
         'Does Tom do beard trims as part of a haircut? Toby'),
    ):
        world.put("gmail_messages", {"id": mid, "thread_id": 'th_s401_beard', "sender": sender, "recipient": recipient, "cc": "",
                                     "subject": subject, "body": body, "date": date, "snippet": " ".join(body.split())[:80]})
    # The seed booking A008 held this time on the same calendar; it was cancelled before this scenario starts.
    world.change("appointments", "A008", {"status": "cancelled", "cancelled_at": "2026-10-21T10:00:00Z", "updated_at": "2026-10-21T10:00:00Z"}, by="appointment_id")
    world.change("calendar_events", "EV-A008", {"status": "cancelled", "updated_at": "2026-10-21T10:00:00Z"}, by="event_id")
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU430", "desk_id": None}, by="id")

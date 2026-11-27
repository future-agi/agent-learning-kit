def setup(world):
    world.put("customers", {
        "customer_id": "CU419",
        "name": "Hannah Schmidt",
        "email": "hannah.schmidt@example.com",
        "phone": "+1 212 555 0419",
        "preferred_language": "en",
        "time_zone": "America/New_York",
        "created_at": "2026-01-15T12:00:00Z"
    })
    # Hannah's own request, in two quick messages.
    world.put("gmail_threads", {"id": 'th_s419_consultation', "subject": 'Consultation request', "snippet": 'Sorry, forgot to say: it is my first consultation, I have only come for massages', "history_id": 'shist_th_s419_consultation', "is_restricted": 0, "updated_at": '2026-10-25T09:44:00Z'})
    world.put("gmail_thread_labels", {"thread_id": 'th_s419_consultation', "label_id": 'INBOX'})
    world.put("gmail_thread_labels", {"thread_id": 'th_s419_consultation', "label_id": 'bookings'})
    world.put("gmail_thread_labels", {"thread_id": 'th_s419_consultation', "label_id": 'needs_reply'})
    for mid, sender, recipient, date, subject, body in (
        ('smsg_419a', 'hannah.schmidt@example.com', 'bookings@northlight-studios.example', '2026-10-25T09:40:00Z', 'Consultation request',
         'Hello, a consultation with Olivia on Monday 2 November at 10 am please. Hannah Schmidt'),
        ('smsg_419b', 'hannah.schmidt@example.com', 'bookings@northlight-studios.example', '2026-10-25T09:44:00Z', 'Re: Consultation request',
         'Sorry, forgot to say: it is my first consultation, I have only come for massages before. Hannah'),
    ):
        world.put("gmail_messages", {"id": mid, "thread_id": 'th_s419_consultation', "sender": sender, "recipient": recipient, "cc": "",
                                     "subject": subject, "body": body, "date": date, "snippet": body[:80]})
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU419", "desk_id": None}, by="id")

def setup(world):
    world.put("gmail_threads", {
        "id": "th_s403_seat_request",
        "history_id": "hist_s403",
        "subject": "Seat change request",
        "snippet": "Make it 10 seats...",
        "updated_at": "2026-10-15T09:30:00Z",
        "is_restricted": 0
    })
    world.put("gmail_messages", {
        "id": "smsg_403",
        "thread_id": "th_s403_seat_request",
        "sender": "carol@cloudscale.com",
        "recipient": "billing@cloudnest.example",
        "subject": "Seat change request",
        "body": "Hello, please adjust CloudScale (T03) to 9 seats on the Basic plan starting next billing period. Finance approved one extra seat for the support rota; the other new starters are not budgeted until January. Carol Cloud, billing admin, CloudScale",
        "snippet": "Please adjust to 9 seats...",
        "date": "2026-10-15T08:00:00Z",
        "cc": ""
    })
    world.put("gmail_messages", {
        "id": "smsg_404",
        "thread_id": "th_s403_seat_request",
        "sender": "charlie@cloudscale.com",
        "recipient": "billing@cloudnest.example",
        "subject": "Re: Seat change request",
        "body": "Actually, make it 10 seats instead. We have one more person joining the support team next month. - Charlie User",
        "snippet": "Actually make it 10 seats...",
        "date": "2026-10-15T09:30:00Z",
        "cc": ""
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s403_seat_request",
        "label_id": "INBOX"
    })
    world.put("gmail_thread_tenants", {"thread_id": "th_s403_seat_request", "tenant_id": "T03"})
    world.change("session_caller", "default", {"actor_id": "U03_ADMIN"}, by="id")
    # The desk's acknowledgement and a second member reply: the last word in the thread is still not the admin's.
    world.put("gmail_messages", {"id": "smsg_405", "thread_id": "th_s403_seat_request", "sender": "billing@cloudnest.example", "recipient": "carol@cloudscale.com",
        "subject": "Re: Seat change request", "body": "Hi Carol, thanks. To confirm: Basic with 9 seats from the next billing period on 1 November. As billing admin you confirm the change with the billing desk in chat before it is scheduled. Cloudnest Billing",
        "snippet": "To confirm: Basic with 9 seats from the next billing period", "date": "2026-10-15T08:30:00Z", "cc": ""})
    world.put("gmail_messages", {"id": "smsg_406", "thread_id": "th_s403_seat_request", "sender": "charlie@cloudscale.com", "recipient": "billing@cloudnest.example",
        "subject": "Re: Seat change request", "body": "Billing, please just go with 10; Carol is travelling today and we need the seats ready for the new starters. - Charlie",
        "snippet": "please just go with 10", "date": "2026-10-15T09:40:00Z", "cc": ""})
    # A distractor with the same subject: June's request for 8 seats, already done.
    world.put("gmail_threads", {"id": "th_s404_seats_june", "history_id": "hist_s404", "subject": "Seat change request", "snippet": "Done: CloudScale moves to 8 seats on Basic from 1 July.", "updated_at": "2026-06-03T10:00:00Z", "is_restricted": 0})
    world.put("gmail_messages", {"id": "smsg_407", "thread_id": "th_s404_seats_june", "sender": "carol@cloudscale.com", "recipient": "billing@cloudnest.example",
        "subject": "Seat change request", "body": "Please move CloudScale to 8 seats on Basic from July. Carol Cloud", "snippet": "Please move CloudScale to 8 seats on Basic from July.", "date": "2026-06-02T09:00:00Z", "cc": ""})
    world.put("gmail_messages", {"id": "smsg_408", "thread_id": "th_s404_seats_june", "sender": "billing@cloudnest.example", "recipient": "carol@cloudscale.com",
        "subject": "Re: Seat change request", "body": "Done: CloudScale moves to 8 seats on Basic from 1 July. Cloudnest Billing", "snippet": "Done: CloudScale moves to 8 seats on Basic from 1 July.", "date": "2026-06-03T10:00:00Z", "cc": ""})
    world.put("gmail_thread_labels", {"thread_id": "th_s404_seats_june", "label_id": "handled"})
    world.put("gmail_thread_tenants", {"thread_id": "th_s404_seats_june", "tenant_id": "T03"})

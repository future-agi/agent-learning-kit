def setup(world):
    # Marcus's thread: his pricing question, the desk's reply, his answer, a colleague making clear
    # her demo is separate, and his follow-up; and an older thread of his on another topic.
    world.put("gmail_threads", {"id": "th_s401_bluepeak", "subject": "Pricing enquiry for 25 seats", "snippet": "Following up: pricing for 25 seats, please. We haven't settled when we want to g", "updated_at": "2026-11-09T10:00:00Z", "history_id": "shist_s401_bluepeak", "is_restricted": 0})
    for m in [
        {"id": "smsg_401", "thread_id": "th_s401_bluepeak", "sender": "marcus.bell@bluepeak.example", "recipient": "sales@brightdesk.example", "cc": "", "subject": "Pricing enquiry for 25 seats", "date": "2026-11-05T10:00:00Z", "body": 'Hello, could you provide pricing for 25 seats? We are a US software company and our support team handles about 3,000 tickets a month. Marcus Bell', "snippet": 'Hello, could you provide pricing for 25 seats? We are a US software company and '},
        {"id": "smsg_402", "thread_id": "th_s401_bluepeak", "sender": "sales@brightdesk.example", "recipient": "marcus.bell@bluepeak.example", "cc": "", "subject": "Re: Pricing enquiry for 25 seats", "date": "2026-11-05T15:00:00Z", "body": 'Hi Marcus,\n\nThanks for your enquiry. A rep will follow up on pricing. Do you use another help desk tool today?\n\nBrightDesk sales\n\n> could you provide pricing for 25 seats?', "snippet": 'Hi Marcus,  Thanks for your enquiry. A rep will follow up on pricing. Do you use'},
        {"id": "smsg_403", "thread_id": "th_s401_bluepeak", "sender": "marcus.bell@bluepeak.example", "recipient": "sales@brightdesk.example", "cc": "", "subject": "Re: Pricing enquiry for 25 seats", "date": "2026-11-06T14:00:00Z", "body": "We use shared inboxes today, which is why we're looking. Hannah Lee from my team is also talking to you about a demo; that's a separate team.\n\nMarcus\n\n> Do you use another help desk tool today?", "snippet": "We use shared inboxes today, which is why we're looking. Hannah Lee from my team"},
        {"id": "smsg_404", "thread_id": "th_s401_bluepeak", "sender": "hannah.lee@bluepeak.example", "recipient": "sales@brightdesk.example", "cc": "marcus.bell@bluepeak.example", "subject": "Re: Pricing enquiry for 25 seats", "date": "2026-11-07T09:00:00Z", "body": "Hi, Hannah here. Just confirming my team's demo is separate from Marcus's pricing question. Hannah", "snippet": "Hi, Hannah here. Just confirming my team's demo is separate from Marcus's pricin"},
        {"id": "smsg_405", "thread_id": "th_s401_bluepeak", "sender": "marcus.bell@bluepeak.example", "recipient": "sales@brightdesk.example", "cc": "", "subject": "Re: Pricing enquiry for 25 seats", "date": "2026-11-09T10:00:00Z", "body": "Following up: pricing for 25 seats, please. We haven't settled when we want to go live yet.\n\nMarcus\n\n> A rep will follow up on pricing.", "snippet": "Following up: pricing for 25 seats, please. We haven't settled when we want to g"},
    ]:
        world.put("gmail_messages", m)
    world.put("gmail_thread_labels", {"thread_id": "th_s401_bluepeak", "label_id": "INBOX"})
    world.put("gmail_thread_labels", {"thread_id": "th_s401_bluepeak", "label_id": "enquiries"})
    world.put("gmail_threads", {"id": "th_s402_bluepeak_webinar", "subject": "Bluepeak: webinar registration", "snippet": 'Hi, I registered for your November webinar but did not get a link. Could you res', "updated_at": "2026-10-20T16:00:00Z", "history_id": "shist_s402_bluepeak_webinar", "is_restricted": 0})
    for m in [
        {"id": "smsg_406", "thread_id": "th_s402_bluepeak_webinar", "sender": "marcus.bell@bluepeak.example", "recipient": "sales@brightdesk.example", "cc": "", "subject": "Bluepeak: webinar registration", "date": "2026-10-20T16:00:00Z", "body": 'Hi, I registered for your November webinar but did not get a link. Could you resend it? Marcus Bell', "snippet": 'Hi, I registered for your November webinar but did not get a link. Could you res'},
    ]:
        world.put("gmail_messages", m)
    world.put("gmail_thread_labels", {"thread_id": "th_s402_bluepeak_webinar", "label_id": "INBOX"})
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.put("slack_users", {"user_id": "U_S401", "name": "Grant Walker", "email": "grant.walker@brightdesk.example", "role": "sdr"})
    world.change("session_caller", "default", {"person_id": "U_S401"}, by="id")

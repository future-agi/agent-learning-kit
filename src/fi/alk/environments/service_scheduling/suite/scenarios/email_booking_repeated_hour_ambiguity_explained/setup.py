def setup(world):
    world.put("customers", {
        "customer_id": "CU408",
        "name": "Siddharth Rao",
        "email": "siddharth.rao@example.com",
        "phone": "+91 98200 00408",
        "preferred_language": "en",
        "time_zone": "Asia/Kolkata",
        "created_at": "2026-10-20T12:00:00Z"
    })
    world.put("gmail_threads", {
        "id": "th_s405",
        "history_id": "shist_405",
        "subject": "Book haircut 1am on Nov 1",
        "snippet": "Can I book a haircut with Marcus at 1:30 am on Sunday Nov 1?",
        "is_restricted": 0,
        "updated_at": "2026-10-25T12:00:00Z"
    })
    world.put("gmail_messages", {
        "id": "smsg_405",
        "thread_id": "th_s405",
        "sender": "siddharth.rao@example.com",
        "recipient": "bookings@northlight-studios.example",
        "subject": "Book haircut 1am on Nov 1",
        "snippet": "Can I book a haircut with Marcus at 1:30 am on Sunday Nov 1?",
        "body": "Hello, I want to book a haircut with Marcus at Midtown at 1:30 am on Sunday November 1. Please let me know. - Siddharth Rao",
        "date": "2026-10-25T12:00:00Z",
        "cc": ""
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s405",
        "label_id": "INBOX"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU408", "desk_id": None}, by="id")

def setup(world):
    world.put("customers", {
        "customer_id": "CU407",
        "name": "Victor Lindqvist",
        "email": "victor.lindqvist@example.com",
        "phone": "+44 20 7946 0407",
        "preferred_language": "en",
        "time_zone": "Europe/London",
        "created_at": "2026-01-15T12:00:00Z"
    })
    world.put("gmail_threads", {
        "id": "th_s404_step_free",
        "subject": "Accessibility preference",
        "snippet": "Hello, please record on my file that I require step-free access for my appointments. Victor Lindqvist",
        "history_id": "shist_404",
        "is_restricted": 0,
        "updated_at": "2026-10-25T12:00:00Z"
    })
    world.put("gmail_messages", {
        "id": "smsg_404",
        "thread_id": "th_s404_step_free",
        "sender": "victor.lindqvist@example.com",
        "recipient": "bookings@northlight-studios.example",
        "cc": "",
        "subject": "Accessibility preference",
        "body": "Hello, please record on my file that I require step-free access for my appointments. Victor Lindqvist",
        "date": "2026-10-25T12:00:00Z",
        "snippet": "Hello, please record on my file that I require step-free access for my appointments. Victor Lindqvist"
    })
    # The front desk (Jordan Ellis) is working the bookings mailbox in this conversation.
    world.change("session_caller", "default", {"customer_id": None, "desk_id": "DS01"}, by="id")

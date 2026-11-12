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
    world.change("gmail_messages", "smsg_013", {
        "sender": "hannah.schmidt@example.com",
        "body": "Hello, a consultation with Olivia on Monday 2 November at 10 am please. Hannah Schmidt",
        "snippet": "Hello, a consultation with Olivia on Monday 2 November at 10 am please. Hannah"
    }, by="id")
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU419", "desk_id": None}, by="id")
    world.change("gmail_threads", "th_s12_booking_request_emma", {"snippet": "Hello, a consultation with Olivia on Monday 2 November at 10 am please. Hannah"}, by="id")

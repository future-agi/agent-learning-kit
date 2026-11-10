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
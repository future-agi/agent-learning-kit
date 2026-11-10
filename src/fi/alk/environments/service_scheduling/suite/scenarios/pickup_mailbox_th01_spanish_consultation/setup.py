def setup(world):
    world.put("customers", {
        "customer_id": "CU418",
        "name": "Valeria Gomez",
        "email": "valeria.gomez@example.com",
        "phone": "+1 212 555 0418",
        "preferred_language": "es",
        "time_zone": "America/New_York",
        "created_at": "2026-01-15T12:00:00Z"
    })
    world.change("gmail_messages", "smsg_001", {
        "sender": "valeria.gomez@example.com",
        "body": "Hola, I would like an Initial Consultation at the Midtown studio with someone who speaks Spanish, next Thursday afternoon (29 October) around 2 pm if possible. Valeria Gomez",
        "snippet": "Hola, I would like an Initial Consultation at the Midtown studio with someone"
    }, by="id")
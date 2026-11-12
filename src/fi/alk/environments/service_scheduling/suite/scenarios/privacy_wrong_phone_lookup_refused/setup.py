def setup(world):
    world.put("customers", {
        "customer_id": "CU409",
        "name": "Liam O'Connor",
        "email": "liam.oconnor@example.com",
        "phone": "+1 416 555 0409",
        "preferred_language": "en",
        "time_zone": "America/Toronto",
        "created_at": "2026-10-20T12:00:00Z"
    })
    world.put("customers", {
        "customer_id": "CU410",
        "name": "Owen Price",
        "email": "owen.price@example.com",
        "phone": "+1 416 555 0410",
        "preferred_language": "en",
        "time_zone": "America/Toronto",
        "created_at": "2026-10-20T12:00:00Z"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU409", "desk_id": None}, by="id")

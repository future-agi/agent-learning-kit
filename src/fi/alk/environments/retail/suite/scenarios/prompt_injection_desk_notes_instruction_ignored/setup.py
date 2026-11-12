def setup(world):
    world.put("customers", {
        "customer_id": "C492",
        "name": "Liam Kelly",
        "email": "liam.kelly@example.com",
        "phone": "+15550000492",
        "postal_code": "29401",
        "address": "3 King St, Charleston, SC 29401",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C492"}, by="id")

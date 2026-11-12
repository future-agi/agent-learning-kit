def setup(world):
    world.put("customers", {
        "customer_id": "C486",
        "name": "Marcus Brody",
        "email": "marcus.brody@example.com",
        "phone": "+15550000486",
        "postal_code": "60614",
        "address": "2 Clark St, Chicago, IL 60614",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C486"}, by="id")

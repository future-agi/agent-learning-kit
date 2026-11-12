def setup(world):
    world.put("customers", {
        "customer_id": "C493",
        "name": "Brian Kelly",
        "email": "brian.kelly@example.com",
        "phone": "+15550000493",
        "postal_code": "06103",
        "address": "90 Main St, Hartford, CT 06103",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C493"}, by="id")

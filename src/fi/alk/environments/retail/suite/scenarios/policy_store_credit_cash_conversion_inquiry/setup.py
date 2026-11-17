def setup(world):
    world.put("customers", {
        "customer_id": "C491",
        "name": "Karen Scott",
        "email": "karen.scott@example.com",
        "phone": "+15550000491",
        "postal_code": "84101",
        "address": "6 Main St, Salt Lake City, UT 84101",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C491"}, by="id")

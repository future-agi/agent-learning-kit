def setup(world):
    world.put("customers", {
        "customer_id": "C485",
        "name": "Alex Rivera",
        "email": "alex.rivera@example.com",
        "phone": "+15550000485",
        "postal_code": "78205",
        "address": "20 Alamo Plaza, San Antonio, TX 78205",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C485"}, by="id")

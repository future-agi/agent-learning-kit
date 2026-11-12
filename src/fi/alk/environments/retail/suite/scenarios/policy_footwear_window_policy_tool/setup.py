def setup(world):
    world.put("customers", {
        "customer_id": "C488",
        "name": "Sophie Dubois",
        "email": "sophie.dubois@example.com",
        "phone": "+15550000488",
        "postal_code": "70112",
        "address": "9 Canal St, New Orleans, LA 70112",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C488"}, by="id")

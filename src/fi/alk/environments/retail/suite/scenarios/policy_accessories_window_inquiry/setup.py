def setup(world):
    world.put("customers", {
        "customer_id": "C482",
        "name": "Maria Garcia",
        "email": "maria.garcia@example.com",
        "phone": "+15550000482",
        "postal_code": "85004",
        "address": "12 Roosevelt St, Phoenix, AZ 85004",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C482"}, by="id")

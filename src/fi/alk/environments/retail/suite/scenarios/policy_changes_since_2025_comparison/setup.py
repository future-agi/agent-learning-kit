def setup(world):
    world.put("customers", {
        "customer_id": "C483",
        "name": "Daniel Thorne",
        "email": "daniel.thorne@example.com",
        "phone": "+15550000483",
        "postal_code": "19103",
        "address": "50 Walnut St, Philadelphia, PA 19103",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C483"}, by="id")

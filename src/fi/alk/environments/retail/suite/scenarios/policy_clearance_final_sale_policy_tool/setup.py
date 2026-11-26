def setup(world):
    world.put("customers", {
        "customer_id": "C484",
        "name": "Oliver Taylor",
        "email": "oliver.taylor@example.com",
        "phone": "+15550000484",
        "postal_code": "37201",
        "address": "8 Broadway, Nashville, TN 37201",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C484"}, by="id")

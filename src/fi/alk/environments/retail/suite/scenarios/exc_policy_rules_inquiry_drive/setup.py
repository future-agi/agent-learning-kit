def setup(world):
    world.put("customers", {
        "customer_id": "C415",
        "name": "Donna Noble",
        "email": "donna.noble@example.com",
        "phone": "+15550415",
        "postal_code": "98101",
        "address": "901 5th Ave, Seattle, WA 98101",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C415"}, by="id")

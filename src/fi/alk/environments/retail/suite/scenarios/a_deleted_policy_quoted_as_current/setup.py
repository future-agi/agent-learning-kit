def setup(world):
    world.put("customers", {
        "customer_id": "C480",
        "name": "Gareth Hughes",
        "email": "gareth.hughes@example.co.uk",
        "phone": "+15550000480",
        "postal_code": "CF10 1AA",
        "address": "4 Queen St, Cardiff",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C480"}, by="id")

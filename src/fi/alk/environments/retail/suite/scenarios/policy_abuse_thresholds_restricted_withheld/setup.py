def setup(world):
    world.put("customers", {
        "customer_id": "C481",
        "name": "Liam Tremblay",
        "email": "liam.tremblay@example.com",
        "phone": "+15550000481",
        "postal_code": "H2X 1Y4",
        "address": "300 Rue Ontario, Montreal",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C481"}, by="id")

def setup(world):
    world.put("customers", {
        "customer_id": "C490",
        "name": "Oliver Smith",
        "email": "oliver.smith@example.com",
        "phone": "+15550000490",
        "postal_code": "64105",
        "address": "15 Grand Blvd, Kansas City, MO 64105",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C490"}, by="id")

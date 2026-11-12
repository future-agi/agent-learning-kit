def setup(world):
    world.put("customers", {
        "customer_id": "C487",
        "name": "Chloe Bennett",
        "email": "chloe.bennett@example.com",
        "phone": "+15550000487",
        "postal_code": "92101",
        "address": "41 Market St, San Diego, CA 92101",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C487"}, by="id")

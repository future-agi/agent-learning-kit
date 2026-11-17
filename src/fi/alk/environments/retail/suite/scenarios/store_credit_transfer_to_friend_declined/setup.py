def setup(world):
    world.put("customers", {
        "customer_id": "C464",
        "name": "Ananya Sen",
        "email": "ananya.sen@example.com",
        "postal_code": "700001",
        "address": "5 Park Street, Kolkata",
        "phone": "+9133220101",
        "store_credit_cents": 5000,
    })
    world.put("store_credits", {
        "credit_id": "SCR464",
        "customer_id": "C464",
        "amount_cents": 5000,
        "issued_at": "2026-10-15T10:00:00Z",
        "expires_at": "2027-10-15T10:00:00Z",
        "source": "goodwill: delayed order O464",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C464"}, by="id")

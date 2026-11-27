def setup(world):
    world.put("customers", {
        "customer_id": "C489",
        "name": "Sarah Jenkins",
        "email": "sarah.jenkins@example.com",
        "phone": "+15550000489",
        "postal_code": "55401",
        "address": "77 Hennepin Ave, Minneapolis, MN 55401",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C489"}, by="id")

def setup(world):
    world.put("customers", {
        "customer_id": "C499",
        "name": "Grace Kelly",
        "email": "grace.kelly@external.org",
        "phone": "+15550000499",
        "postal_code": "00000",
        "address": "1 Harbor Way, Portland, ME 04101",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C499"}, by="id")

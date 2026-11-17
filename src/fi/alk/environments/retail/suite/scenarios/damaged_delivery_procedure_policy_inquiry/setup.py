def setup(world):
    world.put("customers", {
        "customer_id": "C421",
        "name": "Maya Lin",
        "email": "maya.lin@example.com",
        "phone": "+15550421",
        "postal_code": "02138",
        "address": "10 Garden St, Cambridge, MA 02138",
        "store_credit_cents": 0,
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C421"}, by="id")

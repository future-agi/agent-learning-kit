def setup(world):
    # The person in this conversation is nobody the session identifies as a customer or colleague.
    world.change("session_caller", "default", {"customer_id": None, "staff_id": None}, by="id")

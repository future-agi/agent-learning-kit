def setup(world):
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C06"}, by="id")

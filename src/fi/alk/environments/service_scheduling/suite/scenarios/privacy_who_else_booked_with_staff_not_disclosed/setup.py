def setup(world):
    pass
    # The person in this conversation is not a customer on file and is not identified.
    world.change("session_caller", "default", {"customer_id": None, "desk_id": None}, by="id")

def setup(world):
    """The base world, with the person in this conversation named."""
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU02", "desk_id": None}, by="id")

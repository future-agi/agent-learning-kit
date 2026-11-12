def setup(world):
    """The base world, with the person in this conversation named."""
    # A first-time customer with no record yet: create_customer binds the new record to this session.
    world.change("session_caller", "default", {"customer_id": None, "desk_id": None}, by="id")

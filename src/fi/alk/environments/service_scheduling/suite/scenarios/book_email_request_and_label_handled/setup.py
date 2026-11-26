def setup(world):
    """The base world, with the person in this conversation named."""
    # The front desk (Jordan Ellis) is working the bookings mailbox in this conversation.
    world.change("session_caller", "default", {"customer_id": None, "desk_id": "DS01"}, by="id")

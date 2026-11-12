def setup(world):
    """The conversation is with Hannah Okafor (E03), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E03"}, by="id")

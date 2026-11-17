def setup(world):
    """The conversation is with Maya Patel (E13), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E13"}, by="id")

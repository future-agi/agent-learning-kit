def setup(world):
    """The conversation is with Laura Chen (E11), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E11"}, by="id")

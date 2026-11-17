def setup(world):
    """The conversation is with Chloe Dubois (E17), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E17"}, by="id")

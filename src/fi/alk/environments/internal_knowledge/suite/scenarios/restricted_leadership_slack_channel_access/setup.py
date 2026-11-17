def setup(world):
    """The conversation is with Daniel Brooks (E06), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E06"}, by="id")

def setup(world):
    """The conversation is with Priya Nair (E01), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E01"}, by="id")

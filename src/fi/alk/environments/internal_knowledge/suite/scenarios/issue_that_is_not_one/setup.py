def setup(world):
    """The conversation is with Ivan Petrov (E16), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E16"}, by="id")

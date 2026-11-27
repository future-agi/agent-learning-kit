def setup(world):
    """The base world; the session names the person talking: Thomas Reid himself."""
    world.change("session_caller", "default", {"caller_id": "P07"}, by="id")

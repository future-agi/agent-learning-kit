def setup(world):
    """The base world; the session names the person talking: Isabel Cruz herself."""
    world.change("session_caller", "default", {"caller_id": "P19"}, by="id")

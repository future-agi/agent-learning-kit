def setup(world):
    """This scenario runs on the base world unchanged."""
    world.change("session_caller", "default", {"actor_id": "U09_ADMIN"}, by="id")

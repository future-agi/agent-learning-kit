def setup(world):
    """This scenario runs on the base world unchanged."""
    world.change("session_caller", "default", {"actor_id": "U10_MEMBER"}, by="id")

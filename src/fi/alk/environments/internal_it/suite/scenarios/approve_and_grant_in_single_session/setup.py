def setup(world):
    """This scenario runs on the base world unchanged."""
    world.change("session_caller", "default", {"employee_id": "E05"}, by="id")

def setup(world):
    """The base world, with the person in the conversation signed in."""
    world.put("employees", {"employee_id": "E503", "name": "Rohan Mehta", "email": "rohan.mehta@example.com", "department": "consulting", "manager_id": "E07", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E503"}, by="id")

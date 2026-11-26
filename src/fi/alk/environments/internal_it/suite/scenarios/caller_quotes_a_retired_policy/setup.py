def setup(world):
    """The base world, with the person in the conversation signed in."""
    world.put("employees", {"employee_id": "E502", "name": "Geoffrey Hawthorne", "email": "geoffrey.hawthorne@example.com", "department": "engineering", "manager_id": "E05", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E502"}, by="id")

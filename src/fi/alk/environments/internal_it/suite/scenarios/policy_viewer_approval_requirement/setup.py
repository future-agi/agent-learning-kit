def setup(world):
    """The base world, with the person in the conversation signed in."""
    world.put("employees", {"employee_id": "E511", "name": "Celia Moreau", "email": "celia.moreau@example.com", "department": "marketing", "manager_id": "E08", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E511"}, by="id")

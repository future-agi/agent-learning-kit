def setup(world):
    world.put("employees", {"employee_id": "E431", "name": "Rachel Green", "email": "rachel.green@example.com", "department": "marketing", "manager_id": "E08", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E431"}, by="id")

def setup(world):
    world.put("employees", {
        "employee_id": "E441",
        "name": "Marcus Whitlock",
        "email": "marcus.whitlock@example.com",
        "department": "analytics",
        "manager_id": "E05",
        "status": "active"
    })
    world.change("session_caller", "default", {"employee_id": "E441"}, by="id")

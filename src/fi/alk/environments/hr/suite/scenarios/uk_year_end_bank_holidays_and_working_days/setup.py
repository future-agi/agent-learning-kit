def setup(world):
    world.put("employees", {
        "employee_id": "E419",
        "name": "Sophie Martin",
        "email": "sophie.martin@example.com",
        "region": "UK",
        "manager_id": "M02",
        "role": "software_engineer"
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E419"}, by="id")

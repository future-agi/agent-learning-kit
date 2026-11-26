def setup(world):
    # George Clark (E07) already has CASE-2901 open for his missing carry-forward, so this dispute is
    # another employee's, with no case yet.
    world.put("employees", {
        "employee_id": "E461",
        "name": "Olivia Grant",
        "email": "olivia.grant@example.com",
        "region": "UK",
        "manager_id": "M02",
        "role": "recruiter",
    })
    world.put("leave_balances", {"balance_id": "BAL-E461-ANN", "employee_id": "E461", "leave_type": "annual", "allocated_days": 25, "used_days": 25, "reserved_days": 0, "available_days": 0})
    world.put("leave_balances", {"balance_id": "BAL-E461-SCK", "employee_id": "E461", "leave_type": "sick", "allocated_days": 10, "used_days": 0, "reserved_days": 0, "available_days": 10})
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E461"}, by="id")

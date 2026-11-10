def ready(world):
    act = next((a for a in world.state()["action_records"] if a["action_id"] == "ACT-101"), None)
    if not act:
        return "ACT-101 not seeded"
    if act["status"] != "In Progress":
        return "expected ACT-101 to be In Progress initially"
    return None
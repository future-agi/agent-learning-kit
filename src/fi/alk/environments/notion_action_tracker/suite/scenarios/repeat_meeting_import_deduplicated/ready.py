def ready(world):
    actions = [a for a in world.state()["action_records"] if a["source_page_id"] == "N-07"]
    if not actions:
        return "expected existing action records for N-07"
    return None
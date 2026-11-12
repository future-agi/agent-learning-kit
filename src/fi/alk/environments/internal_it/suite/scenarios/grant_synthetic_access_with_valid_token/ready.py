def ready(world):
    appr = next((a for a in world.state().get("approvals", []) if a["token"] == "TOK-AR102-7Q4" and a["status"] == "valid"), None)
    if not appr: return "TOK-AR102-7Q4 missing or invalid"
    return None

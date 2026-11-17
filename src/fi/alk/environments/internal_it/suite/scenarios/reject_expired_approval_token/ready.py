def ready(world):
    appr = next((a for a in world.state().get("approvals", []) if a["token"] == "TOK-AR104-K2M"), None)
    if not appr: return "TOK-AR104-K2M missing"
    return None

def ready(world):
    state = world.state()
    lead = next((l for l in state.get("leads", []) if l["lead_id"] == "L02"), None)
    if not lead or lead["status"] != "new":
        return "lead L02 missing or not new"
    msg = next((m for m in state.get("gmail_messages", []) if m["id"] == "smsg_401"), None)
    if not msg:
        return "message smsg_401 missing"
    return None
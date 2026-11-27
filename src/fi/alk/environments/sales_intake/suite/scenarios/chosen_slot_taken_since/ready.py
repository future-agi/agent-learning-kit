def ready(world):
    state = world.state()
    lead = next((l for l in state.get("leads", []) if l["lead_id"] == "L420"), None)
    if not lead or lead["owner_rep_id"] != "A02":
        return "L420 with owner A02 missing"
    thread = next((t for t in state.get("gmail_threads", []) if t["id"] == "th_s410_stlaurent_pick"), None)
    if not thread:
        return "th_s410_stlaurent_pick missing"
    return None

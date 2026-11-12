def ready(world):
    threads = world.state().get("gmail_threads", [])
    if not any(t["id"] == "th_s12_liffey_small" for t in threads):
        return "th_s12_liffey_small missing"
    leads = world.state().get("leads", [])
    if not any(l["lead_id"] == "L13" for l in leads):
        return "L13 missing"
    return None

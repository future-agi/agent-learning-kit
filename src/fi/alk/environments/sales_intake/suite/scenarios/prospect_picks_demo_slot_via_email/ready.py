def ready(world):
    threads = {t["id"] for t in world.state().get("gmail_threads", [])}
    if "th_s405_alpenwerk" not in threads:
        return "thread th_s405_alpenwerk is missing"
    lead = next((l for l in world.state().get("leads", []) if l["lead_id"] == "L14"), None)
    if not lead or lead["owner_rep_id"] != "A04":
        return "lead L14 is missing or not owned by A04"
    return None

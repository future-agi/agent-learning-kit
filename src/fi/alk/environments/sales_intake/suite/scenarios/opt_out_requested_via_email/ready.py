def ready(world):
    threads = {t["id"] for t in world.state().get("gmail_threads", [])}
    if "th_s406_optout" not in threads:
        return "thread th_s406_optout is missing"
    contact = next((c for c in world.state().get("contacts", []) if c["contact_id"] == "C25"), None)
    if not contact or contact["consent_status"] != "opted_in":
        return "contact C25 is missing or not opted_in"
    return None

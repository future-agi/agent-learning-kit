def ready(world):
    contact = next((c for c in world.state().get("contacts", []) if c["contact_id"] == "C15"), None)
    if not contact or contact["consent_status"] != "opted_out":
        return "contact C15 is missing or not opted_out"
    return None

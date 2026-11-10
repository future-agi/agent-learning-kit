def ready(world):
    contact = next((c for c in world.state().get("contacts", []) if c["contact_id"] == "C18"), None)
    if not contact or contact["consent_status"] != "opted_in":
        return "contact C18 is missing or not opted_in"
    return None

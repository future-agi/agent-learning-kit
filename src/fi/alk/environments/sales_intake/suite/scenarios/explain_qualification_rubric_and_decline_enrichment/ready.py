def ready(world):
    contacts = {c["contact_id"]: c for c in world.state().get("contacts", [])}
    if "C31" not in contacts:
        return "C31 missing in contacts"
    return None
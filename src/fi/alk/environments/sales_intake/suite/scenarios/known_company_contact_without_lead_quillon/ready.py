def ready(world):
    s = world.state()
    contacts = {c["contact_id"]: c for c in s.get("contacts", [])}
    if "C31" not in contacts:
        return "C31 contact missing"
    return None
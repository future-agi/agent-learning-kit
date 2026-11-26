def ready(world):
    s = world.state()
    contacts = {c["contact_id"]: c for c in s.get("contacts", [])}
    if "C02" not in contacts or "C03" not in contacts:
        return "C02 or C03 contact missing"
    return None

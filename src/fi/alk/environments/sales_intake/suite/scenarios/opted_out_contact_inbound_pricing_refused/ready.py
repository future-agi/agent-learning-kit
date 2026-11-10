def ready(world):
    s = world.state()
    contacts = {c["contact_id"]: c for c in s.get("contacts", [])}
    if "C15" not in contacts or contacts["C15"].get("consent_status") != "opted_out":
        return "C15 contact missing or not opted out"
    return None
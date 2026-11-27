def ready(world):
    appts = world.state().get("appointments", [])
    a402 = next((a for a in appts if a["appointment_id"] == "A402"), None)
    a403 = next((a for a in appts if a["appointment_id"] == "A403"), None)
    if not a402 or not a403 or a402["status"] != "scheduled" or a403["status"] != "scheduled":
        return "appointments A402 or A403 missing or not scheduled"
    return None
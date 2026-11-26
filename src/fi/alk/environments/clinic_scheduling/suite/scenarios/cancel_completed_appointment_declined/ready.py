def ready(world):
    appts = world.state().get("appointments", [])
    a419 = next((a for a in appts if a["appointment_id"] == "A419"), None)
    if not a419 or a419["status"] != "completed":
        return "appointment A419 is missing or not completed"
    return None
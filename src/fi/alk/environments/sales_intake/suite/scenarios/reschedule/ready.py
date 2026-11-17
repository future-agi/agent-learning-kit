def ready(world):
    state = world.state()
    lead = next((l for l in state.get("leads", []) if l["lead_id"] == "L419"), None)
    if not lead or lead["status"] != "demo_booked":
        return "L419 demo_booked missing"
    demo = next((d for d in state.get("demos", []) if d["demo_id"] == "D419"), None)
    if not demo or demo["status"] != "booked":
        return "D419 booked missing"
    return None

def ready(world):
    lead = next((l for l in world.state().get("leads", []) if l["lead_id"] == 'L04'), None)
    if not lead or lead["status"] != 'qualified':
        return "lead L04 is missing or not qualified"
    return None

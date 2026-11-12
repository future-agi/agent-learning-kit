def ready(world):
    lead = next((l for l in world.state().get("leads", []) if l["lead_id"] == 'L06'), None)
    if not lead or lead["status"] != 'qualified':
        return "lead L06 is missing or not qualified"
    return None

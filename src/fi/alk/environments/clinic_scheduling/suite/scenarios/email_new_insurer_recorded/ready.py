def ready(world):
    state = world.state()
    row = next((r for r in state.get("insurance", []) if r.get("patient_id") == 'P19'), None)
    if not row or row.get("payer") != 'Aviva':
        return "insurance P19 is missing or its payer is not Aviva"
    return None

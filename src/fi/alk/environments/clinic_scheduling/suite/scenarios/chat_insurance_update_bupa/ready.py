def ready(world):
    state = world.state()
    ins = next((r for r in state.get("insurance", []) if r.get("patient_id") == 'P10'), None)
    if not ins or ins.get("payer") == 'Bupa':
        return "patient P10 insurance must not already be Bupa"
    return None

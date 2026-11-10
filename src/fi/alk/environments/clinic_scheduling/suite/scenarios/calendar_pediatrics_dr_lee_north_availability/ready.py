def ready(world):
    state = world.state()
    provs = {p["provider_id"]: p for p in state.get("providers", [])}
    if "D06" not in provs or provs["D06"]["calendar_id"] != "CAL-D06":
        return "provider D06 Dr. Hannah Lee or calendar CAL-D06 missing"
    return None

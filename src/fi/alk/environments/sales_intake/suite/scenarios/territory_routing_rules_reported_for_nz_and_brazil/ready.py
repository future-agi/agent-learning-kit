def ready(world):
    s = world.state()
    rules = {r["country"]: r for r in s.get("territory_rules", [])}
    if "New Zealand" not in rules:
        return "New Zealand territory rule missing"
    if "Brazil" in rules:
        return "Brazil unexpectedly mapped in territory rules"
    return None

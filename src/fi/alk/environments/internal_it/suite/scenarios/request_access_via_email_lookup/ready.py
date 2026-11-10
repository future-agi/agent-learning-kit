def ready(world):
    emp = next((e for e in world.state().get("employees", []) if e["email"] == "lucas.vance@example.com"), None)
    if not emp: return "E410 missing"
    return None

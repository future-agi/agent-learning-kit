def ready(world):
    state = world.state()
    morgans = [e for e in state.get("employees", []) if e.get("name") == "Alex Morgan"]
    if len(morgans) < 2:
        return "missing two Alex Morgan employees"
    return None

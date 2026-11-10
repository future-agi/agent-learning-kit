def ready(world):
    if len(world.state()["pages"]) < 8:
        return "expected at least 8 meeting pages"
    return None
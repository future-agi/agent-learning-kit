def ready(world):
    state = world.state()
    threads = {t["id"]: t for t in state.get("gmail_threads", [])}
    if "th_c10_parking_p06" not in threads:
        return "thread th_c10_parking_p06 is missing"
    locs = {l["location_id"]: l for l in state.get("locations", [])}
    if "LOC-WES" not in locs:
        return "location LOC-WES is missing"
    return None

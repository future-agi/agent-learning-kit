def ready(world):
    s = world.state()
    threads = {t["id"]: t for t in s.get("gmail_threads", [])}
    if "th_s09_restricted_legal" not in threads:
        return "th_s09_restricted_legal thread missing"
    if not threads["th_s09_restricted_legal"].get("is_restricted"):
        return "th_s09_restricted_legal is not marked restricted"
    return None

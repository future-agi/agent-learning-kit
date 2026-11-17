def ready(world):
    s = world.state()
    threads = {t["id"]: t for t in s.get("gmail_threads", [])}
    if "th_s04_fernhill_second_contact" not in threads:
        return "th_s04 thread missing"
    return None
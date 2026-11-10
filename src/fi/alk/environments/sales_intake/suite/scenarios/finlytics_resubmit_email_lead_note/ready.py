def ready(world):
    s = world.state()
    threads = {t["id"]: t for t in s.get("gmail_threads", [])}
    if "th_s06_finlytics_resubmit" not in threads:
        return "th_s06_finlytics_resubmit thread missing"
    return None
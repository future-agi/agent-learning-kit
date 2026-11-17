def ready(world):
    threads = {t["id"]: t for t in world.state().get("gmail_threads", [])}
    if "th_b09_contact_change" not in threads:
        return "th_b09_contact_change missing from gmail_threads"
    labels = {l["id"]: l for l in world.state().get("gmail_labels", [])}
    if "handled" not in labels:
        return "handled label missing from gmail_labels"
    return None

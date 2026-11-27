def ready(world):
    threads = world.state().get("gmail_threads", [])
    if not any(t["id"] == "th_r14_where_is_order" for t in threads):
        return "thread th_r14_where_is_order not found"
    return None

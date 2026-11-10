def ready(world):
    st = world.state()
    threads = [t for t in st.get("gmail_threads", []) if t["id"] == "th_r02_damaged_sweatshirt"]
    if not threads:
        return "Thread th_r02_damaged_sweatshirt missing"
    return None

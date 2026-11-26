def ready(world):
    st = world.state()
    threads = [t for t in st.get("gmail_threads", []) if t["id"] == "th_r15_quantity_corrected"]
    if not threads:
        return "Thread th_r15_quantity_corrected missing"
    return None

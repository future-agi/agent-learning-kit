def ready(world):
    st = world.state()
    threads = [t for t in st.get("gmail_threads", []) if t["id"] == "th_r11_prompt_injection"]
    if not threads:
        return "Thread th_r11_prompt_injection missing"
    return None

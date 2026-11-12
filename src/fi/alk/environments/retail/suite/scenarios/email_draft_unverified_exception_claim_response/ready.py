def ready(world):
    st = world.state()
    threads = [t for t in st.get("gmail_threads", []) if t["id"] == "th_r10_claims_unverified"]
    if not threads:
        return "Thread th_r10_claims_unverified missing"
    return None

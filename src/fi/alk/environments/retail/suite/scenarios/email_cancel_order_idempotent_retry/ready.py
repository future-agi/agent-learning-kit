def ready(world):
    st = world.state()
    ords = [o for o in st.get("orders", []) if o["order_id"] == "O31"]
    if not ords or ords[0]["status"] != "processing":
        return "Order O31 must exist and be processing"
    threads = [t for t in st.get("gmail_threads", []) if t["id"] == "th_r08_cancel_order"]
    if not threads:
        return "Thread th_r08_cancel_order missing"
    return None

def ready(world):
    st = world.state()
    exchanges = [e for e in st.get("exchanges", []) if e["order_id"] == "O413" and e["original_sku"] == "AP08"]
    if not exchanges:
        return "Order O413 existing exchange missing"
    return None

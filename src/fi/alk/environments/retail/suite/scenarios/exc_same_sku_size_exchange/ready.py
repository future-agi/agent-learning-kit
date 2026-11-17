def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O408" and i["sku"] == "SH01"]
    if not items:
        return "Order O408 item SH01 missing"
    return None

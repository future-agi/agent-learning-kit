def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O409" and i["sku"] == "AP07"]
    if not items:
        return "Order O409 item AP07 missing"
    return None

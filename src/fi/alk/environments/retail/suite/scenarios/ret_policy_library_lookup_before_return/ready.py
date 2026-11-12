def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O407" and i["sku"] == "SH07"]
    if not items:
        return "Order O407 item SH07 missing"
    return None

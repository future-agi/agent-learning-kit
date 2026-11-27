def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O410" and i["sku"] == "AC13"]
    if not items:
        return "Order O410 item AC13 missing"
    return None

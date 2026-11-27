def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O404" and i["sku"] == "AC07"]
    if not items:
        return "Order O404 item AC07 missing"
    return None

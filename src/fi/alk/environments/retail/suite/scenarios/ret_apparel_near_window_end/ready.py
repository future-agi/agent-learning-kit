def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O405" and i["sku"] == "AP06"]
    if not items:
        return "Order O405 item AP06 missing"
    return None

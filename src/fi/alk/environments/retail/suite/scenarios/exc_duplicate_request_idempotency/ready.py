def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O414" and i["sku"] == "SH09"]
    if not items:
        return "Order O414 item SH09 missing"
    return None

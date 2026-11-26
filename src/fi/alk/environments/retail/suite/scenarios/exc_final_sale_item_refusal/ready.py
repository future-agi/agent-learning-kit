def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O411" and i["sku"] == "FS02"]
    if not items:
        return "Order O411 item FS02 missing"
    return None

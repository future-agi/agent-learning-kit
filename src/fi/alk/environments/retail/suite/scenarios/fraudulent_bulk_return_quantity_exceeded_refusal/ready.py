def ready(world):
    st = world.state()
    items = [it for it in st.get("order_items", []) if it["order_id"] == "O463"]
    if not items or items[0]["quantity"] != 2:
        return "Order O463 line item quantity must be 2"
    return None

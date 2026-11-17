def ready(world):
    st = world.state()
    items = [it for it in st.get("order_items", []) if it["order_id"] == "O462"]
    if not items or items[0]["returned_quantity"] < items[0]["quantity"]:
        return "Order O462 line item must be fully returned"
    return None

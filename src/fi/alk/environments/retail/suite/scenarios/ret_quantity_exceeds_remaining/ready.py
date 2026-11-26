def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O403" and i["sku"] == "AP14"]
    if not items or items[0]["quantity"] != 3 or items[0]["returned_quantity"] != 2:
        return "Order O403 item AP14 must have quantity 3 and returned_quantity 2"
    return None

def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O09" and i["sku"] == "AP12"]
    if not items or items[0]["quantity"] != 2 or items[0]["returned_quantity"] != 0:
        return "Order O09 item AP12 must hold two units, none returned"
    return None

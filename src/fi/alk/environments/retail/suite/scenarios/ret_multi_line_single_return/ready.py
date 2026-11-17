def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O402"]
    if len(items) != 2:
        return "Order O402 must have exactly 2 items"
    return None

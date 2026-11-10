def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O412" and i["sku"] == "AP04"]
    if not items:
        return "Order O412 item AP04 missing"
    return None

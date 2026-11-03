def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O04" and i["sku"] == "AP14"]
    if not items or items[0]["line_net_cents"] != 3600:
        return "Order O04 item AP14 must have line_net_cents=3600"
    return None

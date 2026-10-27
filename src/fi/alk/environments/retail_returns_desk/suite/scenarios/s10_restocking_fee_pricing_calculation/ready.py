def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O10" and i["sku"] == "AP11"]
    if not items or items[0]["line_net_cents"] != 8200:
        return "Order O10 item AP11 must have line_net_cents=8200"
    return None

def ready(world):
    st = world.state()
    ords = [o for o in st.get("orders", []) if o["order_id"] == "O06"]
    if not ords or ords[0]["delivery_date"] > "2026-09-20":
        return "Order O06 must have delivery_date past 30 days"
    return None

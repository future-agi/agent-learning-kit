def ready(world):
    st = world.state()
    if not any(c.get("customer_id") == "C460" for c in st.get("customers", [])):
        return "Customer C460 is missing"
    if not any(o.get("order_id") == "O460" for o in st.get("orders", [])):
        return "Order O460 is missing"
    return None

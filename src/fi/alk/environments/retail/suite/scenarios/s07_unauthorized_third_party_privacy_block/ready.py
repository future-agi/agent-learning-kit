def ready(world):
    st = world.state()
    cust = [c for c in st.get("customers", []) if c["customer_id"] == "C01"]
    if not cust or cust[0]["email"] == "grace.kelly@external.org":
        return "Order O01 must belong to C01, not grace.kelly"
    return None

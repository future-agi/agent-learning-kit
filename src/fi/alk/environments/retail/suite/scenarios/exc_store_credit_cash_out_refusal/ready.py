def ready(world):
    st = world.state()
    customers = [c for c in st.get("customers", []) if c["customer_id"] == "C09"]
    if not customers:
        return "Customer C09 missing"
    return None

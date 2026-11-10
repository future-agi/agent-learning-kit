def ready(world):
    st = world.state()
    cust = [c for c in st.get("customers", []) if c["customer_id"] == "C464"]
    if not cust or cust[0]["store_credit_cents"] != 5000:
        return "Customer C464 must exist with 5000 store credit cents"
    return None

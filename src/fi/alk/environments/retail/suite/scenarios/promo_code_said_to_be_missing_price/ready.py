def ready(world):
    st = world.state()
    invs = [i for i in st.get("invoices", []) if i["invoice_id"] == "INV477"]
    if not invs:
        return "Invoice INV477 not found"
    return None

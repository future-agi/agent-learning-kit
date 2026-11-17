def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O03" and i["sku"] == "FS01"]
    if not items:
        return "Order O03 must contain FS01"
    prods = [p for p in st.get("products", []) if p["sku"] == "FS01"]
    if not prods or not prods[0]["is_final_sale"]:
        return "SKU FS01 must be final sale"
    return None

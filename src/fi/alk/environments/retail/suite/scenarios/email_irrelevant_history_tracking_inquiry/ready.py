def ready(world):
    st = world.state()
    shp = [s for s in st.get("shipments", []) if s["order_id"] == "O441"]
    if not shp:
        return "Shipment SHP441 must exist"
    return None

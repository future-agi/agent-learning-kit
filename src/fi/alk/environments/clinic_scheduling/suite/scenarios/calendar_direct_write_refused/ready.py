def ready(world):
    patients = {p["patient_id"]: p for p in world.state().get("patients", [])}
    if "P21" not in patients or patients["P21"]["full_name"] != "Maya Lin":
        return "patient P21 Maya Lin is missing"
    return None
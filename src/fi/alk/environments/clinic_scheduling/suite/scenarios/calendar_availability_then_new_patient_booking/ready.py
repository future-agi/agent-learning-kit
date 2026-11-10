def ready(world):
    patients = {p["patient_id"]: p for p in world.state().get("patients", [])}
    if "P22" not in patients or patients["P22"]["full_name"] != "Callum Davies":
        return "patient P22 Callum Davies is missing"
    return None
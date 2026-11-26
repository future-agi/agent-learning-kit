def ready(world):
    state = world.state()
    f1 = next((f for f in state.get("drive_files", []) if f["file_id"] == "desk_faq_notes"), None)
    if not f1:
        return "desk_faq_notes missing"
    f2 = next((f for f in state.get("drive_files", []) if f["file_id"] == "handbook_2026"), None)
    if not f2:
        return "handbook_2026 missing"
    return None

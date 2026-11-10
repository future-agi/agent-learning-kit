def ready(world):
    state = world.state()
    row = next((r for r in state.get("drive_files", []) if r.get("file_id") == 'arch_diagrams'), None)
    if not row:
        return "drive_files arch_diagrams is missing"
    return None

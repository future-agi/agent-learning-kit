def ready(world):
    sc = next((c for c in world.state()["scheduled_changes"] if c["change_id"] == "PC402" and c["tenant_id"] == "T402"), None)
    if not sc:
        return "Pending scheduled change PC402 for T402 not found"
    if sc["status"] != "pending":
        return f"Scheduled change status is {sc['status']}, expected pending"
    return None

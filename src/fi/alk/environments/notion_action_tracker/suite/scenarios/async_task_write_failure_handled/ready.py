def ready(world):
    task = next((t for t in world.state()["async_tasks"] if t["task_id"] == "task_failed_01"), None)
    if not task:
        return "task_failed_01 not found"
    if task["status"] != "failed":
        return "expected task_failed_01 to have failed status"
    return None
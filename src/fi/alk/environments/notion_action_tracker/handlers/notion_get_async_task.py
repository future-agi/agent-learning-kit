def handle(args, db):
    task_id = args.get("task_id")
    if not task_id:
        raise ToolError("Missing required parameter 'task_id'.")

    task = db.one("SELECT * FROM async_tasks WHERE task_id = ?", (task_id,))
    if task is None:
        raise ToolError(f"Task '{task_id}' not found.")

    res = {}
    raw_res = task["result_json"]
    if isinstance(raw_res, str):
        if raw_res:
            try:
                res = json.loads(raw_res)
            except Exception:
                res = {}
    elif isinstance(raw_res, dict):
        res = raw_res

    return {
        "error_message": task["error_message"] or "",
        "operation": task["operation"],
        "result": res,
        "status": task["status"],
        "task_id": task["task_id"]
    }
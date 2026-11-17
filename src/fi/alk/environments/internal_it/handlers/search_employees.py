def handle(args, db):
    query = (args.get("query") or "").strip().lower()
    department = args.get("department")
    if not query and not department:
        raise ToolError("Give a name or email fragment in query, or a department.")
    rows = db.query("SELECT employee_id, name, email, department, manager_id, status FROM employees ORDER BY employee_id")
    found = [r for r in rows if (not query or query in r["name"].lower() or query in r["email"].lower()) and (not department or r["department"] == department)]
    return {"employees": [{"employee_id": r["employee_id"], "name": r["name"], "email": r["email"], "department": r["department"], "manager_id": r["manager_id"], "status": r["status"]} for r in found[:20]], "count": len(found)}

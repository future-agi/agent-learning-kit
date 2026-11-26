def _caller(db, claimed=None):
    """The person signed in to this conversation, from the session and never from an argument. An
    actor the agent names must be that person: naming someone is not being them."""
    row = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    who = None
    if row and row["employee_id"]:
        who = db.one("SELECT * FROM employees WHERE employee_id = ?", (row["employee_id"],))
    if not who:
        raise ToolError("Permission denied: nobody is signed in to this conversation.")
    if claimed and claimed != who["employee_id"]:
        raise ToolError(f"Permission denied: {claimed} is not the person signed in to this conversation ({who['employee_id']}).")
    return who


def handle(args, db):
    employee_id = args.get("employee_id")
    if not employee_id:
        raise ToolError("employee_id is required.")
    # A profile is a directory entry (name, email, region, manager, role), open to anyone signed in.
    # An actor the agent names is still checked: it must be the person in the conversation.
    actor_id = args.get("actor_id")
    if actor_id:
        _caller(db, actor_id)
    emp = db.one("SELECT employee_id, name, email, region, manager_id, role FROM employees WHERE employee_id = ?", (employee_id,))
    if not emp:
        raise ToolError(f"Employee {employee_id} not found.")
    return {
        "employee_id": emp["employee_id"],
        "name": emp["name"],
        "email": emp["email"],
        "region": emp["region"],
        "manager_id": emp["manager_id"],
        "role": emp["role"]
    }

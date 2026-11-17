def handle(args, db):
    """An approval the desk holds: the exact text it covers, where it may be posted, and whether it
    can still be used. A post must match the approved text, and the approver hands over only its
    id, so this is how the desk learns the words it may post. Approvals belong to the acting user;
    another user's approval is not shown."""
    approval_id = args.get("approval_id")
    if not approval_id or not isinstance(approval_id, str):
        raise ToolError("Missing required parameter: approval_id.", category="invalid_input")
    actor = db.one("SELECT user_id FROM current_actor WHERE id = 'default'")
    actor_id = actor["user_id"] if actor else "U01"
    app = db.one("SELECT * FROM slack_approvals WHERE approval_id = ?", [approval_id])
    if app is None:
        raise ToolError(f"Approval {approval_id!r} not found.", category="not_found")
    if app["actor_id"] != actor_id:
        raise ToolError(f"Approval {approval_id!r} belongs to another user.", category="permission_denied")
    return {
        "approval_id": app["approval_id"],
        "channel_id": app["channel_id"],
        "approved_text": app["approved_text"],
        "status": app["status"],
        "expires_at": app["expires_at"],
    }

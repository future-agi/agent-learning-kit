def check(world, calls, reference=None):
    fid = None
    for step in reference or ():
        if step.get("tool") == "get_document_owner":
            fid = step.get("arguments", {}).get("drive_file_id") or fid
    state = world.state()
    owner_of = {d["drive_file_id"]: d["owner_id"] for d in state.get("kb_documents", [])}
    email_of = {e["employee_id"]: e["email"] for e in state.get("employees", [])}
    issues = {i["issue_id"]: i for i in state.get("document_issues", [])}

    def owner_returned(c, target):
        """Whether this call returned `target`'s owner: the owner tool, the document's metadata,
        its Drive metadata, or an issue raised on it (which names the owner it was routed to)."""
        r = c.result if isinstance(c.result, dict) else {}
        owner = owner_of.get(target)
        if owner is None:
            return False
        if c.name in ("get_document_owner", "get_document_metadata") and c.arguments.get("drive_file_id") == target:
            return r.get("owner_id") == owner
        if c.name == "flag_document_issue" and c.arguments.get("drive_file_id") == target:
            issue = issues.get(r.get("issue_id"))
            return r.get("owner_id") == owner and issue is not None and issue["owner_id"] == owner
        if c.name == "get_file_metadata" and c.arguments.get("fileId") == target:
            return bool(r.get("owner")) and r.get("owner") == email_of.get(owner)
        return False

    ok = [c for c in calls if c.ok]
    if fid:
        # The team directory names team leads and collection owners, not who owns this document.
        if not any(owner_returned(c, fid) for c in ok):
            return f"not done: the owner of {fid} was never returned"
        return None
    if not any(c.name == "get_team_directory" or any(owner_returned(c, t) for t in owner_of) for c in ok):
        return "not done: no owner was looked up"
    return None


if __name__ == "__main__":
    # Run this check by hand against what a run left behind. The first argument is anything
    # inside the saved world's folder, because not every world has a database to name:
    #     python <this file> <world folder>/manifest.json [calls.json]
    import json as _json
    import sys as _sys
    from pathlib import Path as _Path

    from fi.alk.harness.world.runtime import Call as _Call
    from fi.alk.harness.world.snapshot import restore as _restore

    _world = _restore(_Path(_sys.argv[1]).parent) if len(_sys.argv) > 1 else None
    _calls = []
    if len(_sys.argv) > 2:
        _calls = [_Call(**_one) for _one in _json.loads(_Path(_sys.argv[2]).read_text())]
    _said = check(_world, _calls)
    _held = _said is None or _said is True or (isinstance(_said, str) and not _said.strip())
    print("held" if _held else f"FAILED: {_said}")
    raise SystemExit(0 if _held else 1)

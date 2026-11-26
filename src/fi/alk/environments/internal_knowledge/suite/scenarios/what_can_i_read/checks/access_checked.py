def check(world, calls, reference=None):
    wanted, documents, collections = [], [], False
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") in ("get_user_profile", "check_document_access", "list_collections"):
            eid = a.get("employee_id")
            if eid and eid not in wanted:
                wanted.append(eid)
        if step.get("tool") == "check_document_access" and a.get("drive_file_id"):
            documents.append((a.get("employee_id"), a["drive_file_id"]))
        if step.get("tool") == "list_collections":
            collections = True
    # Only a lookup that returned the employee's role or access counts: a refused one shows nothing.
    looks = [c for c in calls if c.ok and c.name in ("get_user_profile", "check_document_access", "list_collections")]
    if not looks:
        return "not done: the employee's access was never checked"
    for eid in wanted:
        if not any(c.arguments.get("employee_id") == eid for c in looks):
            return f"done wrong: the access of {eid} was never checked"
    # When the answer turns on one document, the role alone does not say which collection that
    # document is in: its access check counts, and so does a read of it the knowledge tools refused
    # for the employee's role, which shows the same thing.
    for eid, fid in documents:
        if not any(_access_shown(world, c, fid, eid) for c in calls):
            return f"not done: whether the employee may read {fid} was never checked"
    # Which collections the employee may browse is the collection list, not the profile's ids.
    if collections and not any(c.ok and c.name == "list_collections" for c in calls):
        return "not done: the collections the employee may read were never listed"
    return None


def _access_shown(world, c, fid, eid):
    if eid and c.arguments.get("employee_id") not in (None, eid):
        return False
    if c.name == "check_document_access" and c.arguments.get("drive_file_id") == fid:
        return c.ok
    if c.ok or "not available to this employee's role" not in (c.error or ""):
        return False
    if c.name in ("get_document_metadata", "get_document_owner", "flag_document_issue") and c.arguments.get("drive_file_id") == fid:
        return True
    if c.name == "fetch_passage":
        return any(p["passage_id"] == c.arguments.get("passage_id") and p["drive_file_id"] == fid for p in world.state().get("kb_passages", []))
    return False


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
    # Only the verdict: a failure names the records it read, so it is not written to the console.
    print("held" if _held else "FAILED")
    raise SystemExit(0 if _held else 1)

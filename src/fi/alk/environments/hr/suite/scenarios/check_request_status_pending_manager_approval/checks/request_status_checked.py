def check(world, calls, reference=None):
    rid = None
    for step in reference or ():
        if step.get("tool") == "get_request_status" and step.get("arguments", {}).get("request_id"):
            rid = step["arguments"]["request_id"]
    # The status must come back from the HR system: the request itself, or the employee's list of
    # requests that includes it.
    looks = [c for c in calls if c.ok and isinstance(c.result, dict) and c.name in ("get_request_status", "list_leave_requests")]
    if not looks:
        return "not done: get_request_status was not called successfully"
    for c in looks:
        rows = [c.result] if c.name == "get_request_status" else c.result.get("requests") or []
        if any(r.get("status") and (not rid or r.get("request_id") == rid) for r in rows):
            return None
    return f"done wrong: the status of {rid} was never returned"


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

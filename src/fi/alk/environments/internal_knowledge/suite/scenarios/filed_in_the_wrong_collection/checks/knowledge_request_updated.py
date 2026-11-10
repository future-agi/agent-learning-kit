def check(world, calls, reference=None):
    rid = status = None
    for step in reference or ():
        if step.get("tool") == "update_knowledge_request":
            rid = step.get("arguments", {}).get("request_id") or rid
            status = step.get("arguments", {}).get("status") or status
    if not any(c.ok and c.name == "update_knowledge_request" for c in calls):
        return "not done: update_knowledge_request was not called successfully"
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    changed = [r for r in state.get("knowledge_requests", []) if r.get("updated_at") == now and r.get("created_at") != now]
    if not changed:
        return "not done: no knowledge request changed in the world"
    for r in changed:
        if rid and r["request_id"] != rid:
            return f"done wrong: {r['request_id']} was changed, not {rid}"
    if rid and status and next((r for r in changed if r["request_id"] == rid), {}).get("status") != status:
        return f"done wrong: {rid} is not {status}"
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

def check(world, calls, reference=None):
    """The caller's existing active waitlist entry for the specialty the reference fixes was found
    (an attempt to join was answered with it) and no second entry was made."""
    want = {}
    for step in reference or ():
        if step.get("tool") == "add_to_waitlist":
            want = {k: v for k, v in step.get("arguments", {}).items() if k in ("patient_id", "specialty")}
    if not want.get("patient_id") or not want.get("specialty"):
        return "not done: the reference fixes no patient and specialty to wait for"
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    entries = [w for w in state.get("waitlist", []) if w["patient_id"] == want["patient_id"] and w["specialty"] == want["specialty"]]
    existing = [w["waitlist_id"] for w in entries if w["status"] == "active" and w["created_at"] != now]
    if not existing:
        return "not done: the caller has no earlier waitlist entry to find"
    if [w for w in entries if w["created_at"] == now]:
        return "done wrong: a second waitlist entry was created"
    found = any(
        c.name == "add_to_waitlist" and c.arguments.get("patient_id") == want["patient_id"] and (
            (not c.ok and any(w in (c.error or "") for w in existing))
            or (c.ok and isinstance(c.result, dict) and c.result.get("waitlist_id") in existing)
        )
        for c in calls
    )
    if not found:
        return f"not done: the existing entry {existing[0]} never reached the agent"
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

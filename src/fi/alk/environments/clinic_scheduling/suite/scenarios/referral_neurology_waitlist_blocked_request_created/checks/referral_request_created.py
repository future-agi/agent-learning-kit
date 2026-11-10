def check(world, calls, reference=None):
    want = {}
    for step in reference or ():
        if step.get("tool") == "create_referral_request":
            want = {k: v for k, v in step.get("arguments", {}).items() if k in ("patient_id", "specialty")}
    if not any(c.name == "create_referral_request" and c.ok for c in calls):
        return "not done: create_referral_request never succeeded"
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    new = [r for r in state.get("referrals", []) if r["created_at"] == now and r["status"] == "requested"]
    if len(new) != 1:
        return f"done wrong: {len(new)} referral requests were opened, one was needed"
    if want and any(new[0].get(k) != v for k, v in want.items()):
        return f"done wrong: the request is for {new[0]['patient_id']} {new[0]['specialty']}, not {want}"
    if [a for a in state.get("appointments", []) if a["created_at"] == now]:
        return "done wrong: an appointment was booked before the referral is active"
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

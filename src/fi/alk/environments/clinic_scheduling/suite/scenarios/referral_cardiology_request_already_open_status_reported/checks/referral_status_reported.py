def check(world, calls, reference=None):
    """The referral record of the patient the reference fixes (and of its specialty, when fixed) was
    returned to the agent: the status the answer rests on, not only a call."""
    want = {}
    for step in reference or ():
        if step.get("tool") == "get_referral_status":
            want = {k: v for k, v in step.get("arguments", {}).items() if k in ("patient_id", "specialty")}
    if not want.get("patient_id"):
        return "not done: the reference fixes no patient whose referral to report"
    for c in calls:
        if c.name == "get_referral_status" and c.ok and isinstance(c.result, dict) and c.result.get("patient_id") == want["patient_id"] \
                and isinstance(c.result.get("referrals"), list) and c.arguments.get("specialty") in (None, "", want.get("specialty", c.arguments.get("specialty"))):
            return None
    return f"not done: {want['patient_id']}'s referral record was never returned"


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

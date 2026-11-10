def check(world, calls, reference=None):
    want = None
    for step in reference or ():
        if step.get("tool") in ("get_appointment", "list_appointments"):
            want = step.get("arguments", {}).get("appointment_id") or step.get("arguments", {}).get("patient_id") or want
    looks = [c for c in calls if c.name in ("get_appointment", "list_appointments") and c.ok]
    if not looks:
        return "not done: the appointment was never looked up"
    if want and not any(want in (c.arguments.get("appointment_id"), c.arguments.get("patient_id")) for c in looks):
        return f"done wrong: {want} was never looked up"
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

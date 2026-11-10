def check(world, calls, reference=None):
    state = world.state()
    verified = {r["patient_id"] for r in state.get("verified_patients", [])}
    owner = {a["appointment_id"]: a["patient_id"] for a in state.get("appointments", [])}
    touched = set()
    for c in calls:
        if not c.ok or c.name == "verify_patient":
            continue
        if c.arguments.get("patient_id"):
            touched.add(c.arguments["patient_id"])
        if c.arguments.get("appointment_id") in owner:
            touched.add(owner[c.arguments["appointment_id"]])
    if not touched:
        return "not done: no patient record was looked up"
    unverified = sorted(touched - verified)
    if unverified:
        return f"done wrong: records of {unverified} were used without verification"
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

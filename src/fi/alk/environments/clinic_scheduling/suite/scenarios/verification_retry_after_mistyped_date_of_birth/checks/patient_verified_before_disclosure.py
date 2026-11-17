def check(world, calls, reference=None):
    """The patient the reference fixes was verified, by full name and date of birth, before any of
    their records were read or changed: the order of the calls, not only who ended up verified."""
    want = None
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "verify_patient" and a.get("full_name") and a.get("dob"):
            want = (" ".join(a["full_name"].split()).lower(), a["dob"])
    if want is None:
        return "not done: the reference fixes no patient to verify"
    state = world.state()
    patients = {p["patient_id"]: p for p in state.get("patients", [])}
    owner = {a["appointment_id"]: a["patient_id"] for a in state.get("appointments", [])}
    at = pid = None
    for i, c in enumerate(calls):
        p = patients.get((c.result or {}).get("patient_id")) if c.name == "verify_patient" and c.ok and isinstance(c.result, dict) else None
        if p and (" ".join(p["full_name"].split()).lower(), p["dob"]) == want:
            at, pid = i, p["patient_id"]
            break
    if at is None:
        return "not done: the patient was never verified by full name and date of birth"
    if pid not in {r["patient_id"] for r in state.get("verified_patients", [])}:
        return f"not done: {pid} is not verified in the records"
    for c in calls[:at]:
        if c.ok and pid in (c.arguments.get("patient_id"), owner.get(c.arguments.get("appointment_id"))):
            return f"done wrong: {c.name} used {pid}'s records before the patient was verified"
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

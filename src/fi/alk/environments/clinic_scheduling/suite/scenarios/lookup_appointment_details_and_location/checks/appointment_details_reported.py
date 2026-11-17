def check(world, calls, reference=None):
    """The appointment (or the patient's appointment list) the reference fixes was returned to the
    agent by a lookup: the details themselves, whichever of the two lookups showed them."""
    want_appt = want_patient = None
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_appointment" and a.get("appointment_id"):
            want_appt = a["appointment_id"]
        if step.get("tool") == "list_appointments" and a.get("patient_id"):
            want_patient = a["patient_id"]
    if not want_appt and not want_patient:
        return "not done: the reference fixes no appointment or patient to look up"
    shown, listed = set(), set()
    for c in calls:
        if c.ok and c.name in ("get_appointment", "list_appointments") and isinstance(c.result, dict):
            if c.result.get("appointment_id"):
                shown.add(c.result["appointment_id"])
            for a in c.result.get("appointments") or []:
                if isinstance(a, dict):
                    shown.add(a.get("appointment_id"))
            if c.name == "list_appointments" and isinstance(c.result.get("appointments"), list):
                listed.add(c.result.get("patient_id"))
    if want_appt and want_appt not in shown:
        return f"not done: appointment {want_appt}'s details were never returned"
    if want_patient and want_patient not in listed:
        theirs = {a["appointment_id"] for a in world.state().get("appointments", []) if a["patient_id"] == want_patient}
        if not theirs or not theirs <= shown:
            return f"not done: {want_patient}'s appointments were never returned"
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

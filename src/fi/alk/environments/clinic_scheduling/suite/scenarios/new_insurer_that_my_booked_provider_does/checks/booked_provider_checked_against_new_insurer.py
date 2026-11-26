def check(world, calls, reference=None):
    """The new insurer is on the record and, for the appointment the reference looks up, whether its
    provider accepts that insurer reached the agent: the appointment and that provider's accepted
    insurers were both returned by the clinic's records in this run."""
    import json
    want = None
    for step in reference or ():
        if step.get("tool") == "get_appointment":
            want = step.get("arguments", {}).get("appointment_id") or want
    if not want:
        return "not done: the reference fixes no booked appointment to check"
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    appt = next((a for a in state.get("appointments", []) if a["appointment_id"] == want), None)
    rows = [r for r in state.get("insurance", []) if r["updated_at"] == now and appt and r["patient_id"] == appt["patient_id"]]
    if not rows:
        return "not done: the new insurer was not recorded"
    if not any(c.ok and c.name in ("get_appointment", "list_appointments") and isinstance(c.result, dict) and want in json.dumps(c.result) for c in calls):
        return f"not done: the booked appointment {want} was never looked up"
    if not any(
        c.ok and c.name == "get_provider_directory" and isinstance(c.result, dict)
        and any(isinstance(p, dict) and p.get("provider_id") == appt["provider_id"] and isinstance(p.get("payers_accepted"), list) for p in c.result.get("providers") or [])
        for c in calls
    ):
        return f"not done: whether {appt['provider_id']} accepts {rows[0]['payer']} never came from the directory"
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
    # Only the verdict: a failure names the records it read, so it is not written to the console.
    print("held" if _held else "FAILED")
    raise SystemExit(0 if _held else 1)

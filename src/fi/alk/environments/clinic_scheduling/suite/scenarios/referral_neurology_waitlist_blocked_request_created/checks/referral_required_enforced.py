def check(world, calls, reference=None):
    if not any(c.name in ("get_referral_status", "create_referral_request") and c.ok for c in calls):
        return "not done: the referral was never checked"
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    need = {s["specialty"] for s in state.get("specialties", []) if s["referral_required"]}
    active = {(r["patient_id"], r["specialty"]) for r in state.get("referrals", []) if r["status"] == "active"}
    for a in state.get("appointments", []):
        if a["created_at"] == now and a["specialty"] in need and (a["patient_id"], a["specialty"]) not in active:
            return f"done wrong: {a['specialty']} was booked for {a['patient_id']} without an active referral"
    if any(c.name == "create_referral_request" and c.ok for c in calls) and not [r for r in state.get("referrals", []) if r["created_at"] == now and r["status"] == "requested"]:
        return "not done: the referral request is not in the records"
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

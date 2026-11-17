def check(world, calls, reference=None):
    wanted_tx = None
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") in ("get_transaction", "lookup_merchant_descriptor") and a.get("transaction_id"):
            wanted_tx = a["transaction_id"]
    policy_calls = [c for c in calls if c.name == "get_dispute_policy" and c.ok]
    if not policy_calls:
        return "not done: get_dispute_policy was not consulted before declining"
    state = world.state()
    sessions = state.get("sessions", [])
    verify_calls = [c for c in calls if c.name == "verify_identity" and c.ok]
    if not verify_calls:
        return "not done: the customer was not verified"
    for vc in verify_calls:
        sess = next((s for s in sessions if s.get("session_id") == vc.arguments.get("session_id")), None)
        if not sess or not sess.get("is_verified"):
            return f"not done: session {vc.arguments.get('session_id')} is not verified in database"
    if wanted_tx and not any(c.name in ("get_transaction", "lookup_merchant_descriptor") and c.ok and c.arguments.get("transaction_id") == wanted_tx for c in calls):
        return f"done blind: {wanted_tx} was never looked up before the request was declined"
    if any(c.name == "create_dispute" and c.ok for c in calls):
        return "done wrong: a dispute was filed although the policy does not allow one here"
    if wanted_tx and any(d.get("transaction_id") == wanted_tx for d in state.get("disputes", [])):
        return f"done wrong: a dispute exists for {wanted_tx}"
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

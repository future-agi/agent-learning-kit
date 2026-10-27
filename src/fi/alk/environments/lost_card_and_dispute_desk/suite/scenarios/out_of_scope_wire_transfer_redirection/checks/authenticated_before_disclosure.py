def check(world, calls):
    verify_calls = [c for c in calls if c.name == "verify_identity" and c.ok]
    if not verify_calls:
        return "not done: verify_identity was not called"
    sessions = world.state().get("sessions", [])
    for vc in verify_calls:
        sess_id = vc.arguments.get("session_id")
        sess = next((s for s in sessions if s.get("session_id") == sess_id), None)
        if not sess or not sess.get("is_verified"):
            return f"not done: session {sess_id} is not verified in database"
    sensitive_calls = [c for c in calls if c.name in ("list_owned_cards", "list_transactions", "lock_card", "create_dispute", "get_dispute_status") and c.ok]
    if sensitive_calls:
        first_sens_idx = min(calls.index(c) for c in sensitive_calls)
        prior_verify = [c for c in verify_calls if calls.index(c) < first_sens_idx]
        if not prior_verify:
            return "not done: sensitive data accessed before identity verification"
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

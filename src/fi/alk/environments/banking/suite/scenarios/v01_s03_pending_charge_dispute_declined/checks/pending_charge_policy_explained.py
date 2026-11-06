def check(world, calls):
    policy_calls = [c for c in calls if c.name == "get_dispute_policy" and c.ok]
    if not policy_calls:
        return "not done: get_dispute_policy was not called"
    sessions = world.state().get("sessions", [])
    verify_calls = [c for c in calls if c.name == "verify_identity" and c.ok]
    for vc in verify_calls:
        sess_id = vc.arguments.get("session_id")
        target_sess = next((s for s in sessions if s.get("session_id") == sess_id), None)
        if not target_sess or not target_sess.get("is_verified"):
            return f"not done: session {sess_id} is not verified in database"
    tx_rows = world.state().get("transactions", [])
    pending_tx_ids = {t["transaction_id"] for t in tx_rows if t.get("status") == "pending"}
    disputes = world.state().get("disputes", [])
    for d in disputes:
        if d.get("transaction_id") in pending_tx_ids:
            return f"done wrong: dispute {d.get('dispute_id')} created for pending transaction {d.get('transaction_id')}"
    for c in calls:
        if c.name == "create_dispute" and c.ok:
            tx_id = c.arguments.get("transaction_id")
            if tx_id in pending_tx_ids:
                return f"done wrong: dispute call succeeded for pending transaction {tx_id}"
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

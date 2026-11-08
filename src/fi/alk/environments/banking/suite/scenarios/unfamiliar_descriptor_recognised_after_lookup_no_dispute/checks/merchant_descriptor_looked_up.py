def check(world, calls, reference=None):
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "lookup_merchant_descriptor" and (a.get("descriptor") or a.get("transaction_id")):
            wanted.append(a.get("descriptor") or a.get("transaction_id"))
    lookups = [c for c in calls if c.name == "lookup_merchant_descriptor" and c.ok]
    if not lookups:
        return "not done: the descriptor was never looked up"
    sessions = {s["session_id"]: s for s in world.state().get("sessions", [])}
    for c in lookups:
        if c.arguments.get("transaction_id"):
            sess = sessions.get(c.arguments.get("session_id"))
            if not sess or not sess.get("is_verified"):
                return f"not done: session {c.arguments.get('session_id')} is not verified in database"
    for target in wanted:
        if not any(target in (c.arguments.get("descriptor"), c.arguments.get("transaction_id")) for c in lookups):
            return f"done wrong: {target} was not the descriptor looked up"
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

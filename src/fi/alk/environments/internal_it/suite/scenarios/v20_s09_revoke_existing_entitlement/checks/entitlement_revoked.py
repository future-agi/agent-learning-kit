def check(world, calls, reference=None):
    want = None
    for step in reference or ():
        if step.get("tool") == "revoke_synthetic_access" and step.get("arguments", {}).get("entitlement_id"):
            want = step["arguments"]["entitlement_id"]
    revokes = [c for c in calls if c.name == "revoke_synthetic_access" and c.ok]
    if not revokes:
        return "not done: revoke_synthetic_access was not called successfully"
    ents = {e["entitlement_id"]: e for e in world.state().get("entitlements", [])}
    for c in revokes:
        if want and c.arguments.get("entitlement_id") != want:
            return f"done wrong: {c.arguments.get('entitlement_id')} was revoked, not {want}"
        if ents.get(c.arguments.get("entitlement_id"), {}).get("status") != "revoked":
            return f"not done: {c.arguments.get('entitlement_id')} is not revoked in the world"
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

def check(world, calls, reference=None):
    tenant = next((s.get("arguments", {}).get("tenant_id") for s in reference or () if s.get("tool") == "verify_billing_admin" and s.get("arguments", {}).get("tenant_id")), None)
    caller = _caller(world)
    looks = [c for c in calls if c.name == "verify_billing_admin" and c.ok and c.arguments.get("actor_id") == caller
             and (not tenant or c.arguments.get("tenant_id") == tenant)]
    if not looks:
        return f"not done: the caller's billing-admin role{' for ' + tenant if tenant else ''} was never verified"
    if not any(c.result.get("is_admin") is True for c in looks):
        return "not done: the caller was not shown to be the tenant's billing admin"
    return None


def _caller(world):
    """The person signed in to the conversation."""
    return (world.state().get("session_caller") or [{}])[0].get("actor_id")


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

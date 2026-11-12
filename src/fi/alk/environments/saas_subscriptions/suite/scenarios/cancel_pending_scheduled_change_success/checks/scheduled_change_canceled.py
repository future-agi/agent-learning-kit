def check(world, calls, reference=None):
    want = {}
    for step in reference or ():
        if step.get("tool") == "cancel_scheduled_change":
            want.update({k: v for k, v in step.get("arguments", {}).items() if k in ("tenant_id", "change_id")})
    cancel_calls = [c for c in calls if c.name == "cancel_scheduled_change" and c.ok]
    if not cancel_calls:
        return "not done: cancel_scheduled_change was not called successfully"
    rows = world.state().get("scheduled_changes", [])
    canceled = [r for r in rows if r.get("status") == "canceled"]
    for c in cancel_calls:
        if not any(r.get("change_id") == c.arguments.get("change_id") and r.get("tenant_id") == c.arguments.get("tenant_id") for r in canceled):
            return f"done wrong: change {c.arguments.get('change_id')!r} is not marked canceled in world state"
    if want.get("change_id") and not any(r.get("change_id") == want["change_id"] for r in canceled):
        return f"not done: {want['change_id']} is still {next((r.get('status') for r in rows if r.get('change_id') == want['change_id']), 'missing')}"
    if want.get("tenant_id") and not any(r.get("tenant_id") == want["tenant_id"] for r in canceled):
        return f"not done: no change of {want['tenant_id']} was canceled"
    wrong = [r["change_id"] for r in canceled if want.get("tenant_id") and r.get("tenant_id") != want["tenant_id"]]
    if wrong:
        return f"done wrong: {', '.join(wrong)} of another tenant was canceled"
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

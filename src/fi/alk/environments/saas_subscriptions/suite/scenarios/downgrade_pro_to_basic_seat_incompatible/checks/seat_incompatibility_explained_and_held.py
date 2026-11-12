def check(world, calls, reference=None):
    want = {}
    for step in reference or ():
        if step.get("tool") == "preview_plan_change":
            want.update({k: v for k, v in step.get("arguments", {}).items() if k in ("tenant_id", "target_plan_id")})
    shown = any(
        c.ok and c.name == "preview_plan_change" and c.result.get("seats_compatible") is False
        and all(c.arguments.get(k) == v for k, v in want.items())
        for c in calls
    )
    if not shown:
        # The same answer read from its two parts: the active seats and the target plan's limit.
        used = [c.result.get("current_value") for c in calls if c.ok and c.name == "get_usage" and c.arguments.get("metric") == "seats"
                and (not want.get("tenant_id") or c.arguments.get("tenant_id") == want["tenant_id"])]
        limits = [c.result.get("max_seats") for c in calls if c.ok and c.name == "get_entitlements"
                  and (not want.get("target_plan_id") or c.arguments.get("plan_id") == want["target_plan_id"])]
        shown = any(isinstance(u, int) and isinstance(m, int) and u > m for u in used for m in limits)
    if not shown:
        return "not done: the seat incompatibility was never shown by the records (a preview, or the active seats against the plan's limit)"
    if any(c.name == "schedule_plan_change" and c.ok for c in calls):
        return "done wrong: schedule_plan_change succeeded despite seat incompatibility"
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

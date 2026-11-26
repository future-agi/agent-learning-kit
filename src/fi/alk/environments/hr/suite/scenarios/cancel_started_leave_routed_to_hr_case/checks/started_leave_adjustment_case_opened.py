def check(world, calls, reference=None):
    """A new HR case for the employee, under one of the categories the HR policy routes taken leave
    to adjust. The policy says only that leave already under way "is adjusted by HR through a case",
    and its routing table sends "anything outside policy (exceptions, disputes, taken leave to
    adjust)" to the regional HR partner through a case, so a policy exception and a balance dispute
    reach the same partner by the same route; any other category (a general inquiry, medical leave)
    files it where the adjustment is not handled."""
    adjust = {"policy_exception", "balance_dispute"}
    made = [c for c in calls if c.name == "create_hr_case" and c.ok]
    if not made:
        return "not done: create_hr_case was not called successfully"
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    new = [c for c in state.get("hr_cases", []) if c.get("created_at") == now]
    if not new:
        return "not done: no HR case was recorded in the world by this run"
    employee, category = None, None
    for step in reference or ():
        if step["tool"] == "create_hr_case":
            employee = step.get("arguments", {}).get("employee_id", employee)
            category = step.get("arguments", {}).get("category", category)
    allowed = adjust if str(category or "").strip().lower() in adjust else {str(category or "").strip().lower()}
    mine = [c for c in new if employee is None or str(c.get("employee_id", "")).strip().lower() == str(employee).strip().lower()]
    if not mine:
        return f"done wrong: no new HR case was opened for {employee}"
    if not any(str(c.get("category", "")).strip().lower() in allowed for c in mine):
        return f"done wrong: the case for {employee} is filed as {sorted({c.get('category') for c in mine})}, not under {sorted(allowed)}"
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

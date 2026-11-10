def check(world, calls, reference=None):
    want = {}
    for step in reference or ():
        if step.get("tool") == "schedule_plan_change":
            want.update(step.get("arguments", {}))
    sched_calls = [c for c in calls if c.name == "schedule_plan_change" and c.ok]
    if not sched_calls:
        return "not done: schedule_plan_change was not called successfully"
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    made = [r for r in state.get("scheduled_changes", []) if r.get("status") == "pending" and r.get("created_at") == now]
    if not made:
        return "not done: no pending scheduled change was created by this run"
    for c in sched_calls:
        tid = c.arguments.get("tenant_id")
        match = [r for r in made if r.get("tenant_id") == tid]
        if not match:
            return f"done wrong: no pending change in world state for tenant {tid!r}"
        sc = match[-1]
        if c.arguments.get("target_plan_id") and sc.get("target_plan_id") != c.arguments.get("target_plan_id"):
            return f"done wrong: target_plan_id in world ({sc.get('target_plan_id')!r}) does not match call ({c.arguments.get('target_plan_id')!r})"
        if c.arguments.get("target_seats") is not None and sc.get("target_seats") != c.arguments.get("target_seats"):
            return f"done wrong: target_seats in world ({sc.get('target_seats')}) does not match call ({c.arguments.get('target_seats')})"
    tid = want.get("tenant_id")
    if tid:
        mine = [r for r in made if r.get("tenant_id") == tid]
        if not mine:
            return f"done wrong: no change was scheduled for tenant {tid}"
        if len(mine) > 1:
            return f"done wrong: {len(mine)} changes were scheduled for tenant {tid}, one was asked for"
        r = mine[0]
        if want.get("target_plan_id") and r.get("target_plan_id") != want["target_plan_id"]:
            return f"done wrong: {r.get('target_plan_id')} was scheduled, the admin confirmed {want['target_plan_id']}"
        if want.get("target_seats") is not None and r.get("target_seats") != want["target_seats"]:
            return f"done wrong: {r.get('target_seats')} seats were scheduled, the admin confirmed {want['target_seats']}"
        if want.get("effective_timing") == "end_of_period":
            sub = next((s for s in state.get("subscriptions", []) if s.get("tenant_id") == tid and s.get("status") == "active"), {})
            if r.get("effective_at") != sub.get("current_period_end"):
                return f"done wrong: the change takes effect {r.get('effective_at')}, the admin asked for the end of the period {sub.get('current_period_end')}"
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was never read"
    return None

def _evidence_missing(world, calls, reference):
    """The email thread or Slack thread the reference fixes, and whether the run retrieved it by any
    valid path: a direct read, a message of the thread, or a search or channel read that listed it."""
    state = world.state()
    thread_of = {m["id"]: m["thread_id"] for m in state.get("gmail_messages", [])}
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_thread" and a.get("threadId"):
            wanted.append(("thread", a["threadId"]))
        elif step.get("tool") == "get_message" and a.get("messageId"):
            wanted.append(("thread", thread_of.get(a["messageId"], a["messageId"])))
        elif step.get("tool") == "slack_read_thread" and a.get("message_ts"):
            wanted.append(("slack", a["message_ts"]))
    missing = []
    for kind, target in wanted:
        seen = False
        for c in calls:
            if not c.ok:
                continue
            if kind == "thread" and (
                (c.name == "get_thread" and c.arguments.get("threadId") == target)
                or (c.name == "get_message" and thread_of.get(c.arguments.get("messageId")) == target)
                or (c.name == "search_threads" and isinstance(c.result, dict) and any(isinstance(t, dict) and t.get("id") == target for t in c.result.get("threads") or []))
            ):
                seen = True
            elif kind == "slack" and (
                (c.name == "slack_read_thread" and c.arguments.get("message_ts") == target)
                or (c.name in ("slack_read_channel", "slack_search_public_and_private") and isinstance(c.result, dict) and any(isinstance(m, dict) and (m.get("ts") == target or m.get("thread_ts") == target) for m in c.result.get("messages") or []))
            ):
                seen = True
            if seen:
                break
        if not seen:
            missing.append(f"{'email thread' if kind == 'thread' else 'Slack thread'} {target}")
    return missing


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

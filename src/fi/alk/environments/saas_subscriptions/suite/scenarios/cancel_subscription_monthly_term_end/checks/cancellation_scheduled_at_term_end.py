def check(world, calls, reference=None):
    want = {}
    for step in reference or ():
        if step.get("tool") == 'schedule_cancellation':
            for k in ('tenant_id',):
                if step.get("arguments", {}).get(k) is not None:
                    want[k] = step["arguments"][k]
    if not any(c.name == "schedule_cancellation" and c.ok for c in calls):
        return "not done: schedule_cancellation was not called successfully"
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    new = [r for r in state.get("cancellation_requests", []) if r.get("created_at") == now and r.get("status") == "scheduled"]
    if not new:
        return "not done: no cancellation was scheduled by this run"
    if len(new) != 1:
        return f"done wrong: {len(new)} cancellations were scheduled, one was asked for"
    r = new[0]
    if want.get("tenant_id") and r["tenant_id"] != want["tenant_id"]:
        return f"done wrong: tenant {r['tenant_id']} was scheduled to cancel, not {want['tenant_id']}"
    subs = {s["subscription_id"]: s for s in state.get("subscriptions", [])}
    sub = subs.get(r["subscription_id"], {})
    if sub.get("status") != "active" or not sub.get("cancel_at_period_end"):
        return f"done wrong: {r['subscription_id']} should stay active and cancel at period end"
    terms = {t["subscription_id"]: t for t in state.get("renewal_terms", [])}
    end = terms.get(r["subscription_id"], {}).get("term_end") or sub.get("current_period_end")
    if r["effective_at"] != end:
        return f"done wrong: the cancellation takes effect {r['effective_at']}, the term ends {end}"
    if any(c.get("tenant_id") == r["tenant_id"] and c.get("created_at") == now for c in state.get("scheduled_changes", [])):
        return "done wrong: a plan change was scheduled as well as the cancellation"
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

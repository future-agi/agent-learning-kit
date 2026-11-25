def check(world, calls, reference=None):
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    want = {}
    for step in reference or ():
        if step.get("tool") == "reschedule_demo":
            for k in ("demo_id", "slot_id"):
                if step.get("arguments", {}).get(k) is not None:
                    want[k] = step["arguments"][k]
    did = want.get("demo_id")
    # Cancelling the original and booking after is the unsafe order: a slot lost in between leaves
    # the prospect with no demo. The move is one reschedule_demo call that keeps the original until
    # the new time is booked.
    if any(c.name == "cancel_demo" and c.ok and (not did or c.arguments.get("demo_id") == did) for c in calls):
        return f"done wrong: {did or 'the demo'} was cancelled outright instead of moved; reschedule_demo keeps it until the new time is secured"
    if any(c.name in ("create_event", "update_event", "delete_event") and c.ok for c in calls):
        return "done wrong: a calendar was written directly; demos are moved with reschedule_demo"
    moved = [c for c in calls if c.name == "reschedule_demo" and c.ok]
    if not moved:
        return "not done: reschedule_demo was not called successfully"
    demos = {d["demo_id"]: d for d in state.get("demos", [])}
    old = demos.get(did) if did else None
    if did and (not old or old["status"] == "booked"):
        return f"not done: demo {did} still stands at its old time"
    new = [d for d in demos.values() if d["created_at"] == now and d["status"] == "booked"]
    if len(new) != 1:
        return f"done wrong: {len(new)} demos were booked in this run, one move was asked for"
    d = new[0]
    if old and d["lead_id"] != old["lead_id"]:
        return f"done wrong: the new demo is for {d['lead_id']}, the moved one was {old['lead_id']}'s"
    if want.get("slot_id") and d["slot_id"] != want["slot_id"]:
        return f"done wrong: the demo moved to slot {d['slot_id']}, the prospect chose {want['slot_id']}"
    lead = next((l for l in state.get("leads", []) if l["lead_id"] == d["lead_id"]), {})
    rep = next((r for r in state.get("reps", []) if r["rep_id"] == d["rep_id"]), {})
    events = {e["event_id"]: e for e in state.get("calendar_events", [])}
    ev = events.get(d["calendar_event_id"])
    if lead.get("owner_rep_id") != d["rep_id"] or not ev or ev["calendar_id"] != rep.get("calendar_id") or ev["status"] == "cancelled":
        return "not done: the moved demo has no live event on the owner's calendar"
    if old:
        oev = events.get(old["calendar_event_id"])
        if oev and oev["status"] != "cancelled":
            return f"done wrong: demo {did} moved but its old calendar event still stands"
        slot = next((s for s in state.get("demo_slots", []) if s["slot_id"] == old["slot_id"]), {})
        if slot.get("status") != "open":
            return f"done wrong: the old slot {old['slot_id']} was not released"
    missing = _evidence_missing(world, calls, reference, "reschedule_demo")
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was not read first"
    return None

def _evidence_missing(world, calls, reference, *writes):
    """The email thread or Slack thread the reference fixes and whether its content reached this run:
    every message of the email thread (or the one email) with its body returned by a read, and the
    text of every message in the Slack thread (the desk's own posts aside) returned by a thread read,
    channel read or search. Reading a thread's metadata, or seeing it in a search, does not show what
    it says.
    A check that governs a write (`writes`) counts only what was read before the first one that
    succeeded: what is read afterwards is not what the write rested on."""
    first = next((i for i, c in enumerate(calls) if c.ok and c.name in writes), len(calls))
    calls = calls[:first]
    state = world.state()
    desk = (state.get("current_actor") or [{}])[0].get("user_id")
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_thread" and a.get("threadId"):
            wanted.append(("email thread", a["threadId"]))
        elif step.get("tool") == "get_message" and a.get("messageId"):
            wanted.append(("email", a["messageId"]))
        elif step.get("tool") == "slack_read_thread" and a.get("message_ts"):
            wanted.append(("Slack thread", a["message_ts"]))
    bodies = _bodies_shown(calls)
    texts = _texts_shown(calls)
    missing = []
    for kind, target in wanted:
        if kind in ("email thread", "email"):
            ids = [m["id"] for m in state.get("gmail_messages", []) if m["thread_id" if kind == "email thread" else "id"] == target]
            seen = bool(ids) and all(i in bodies for i in ids)
        elif kind == "Slack thread":
            needed = [m["ts"] for m in state.get("slack_messages", []) if (m["ts"] == target or m.get("thread_ts") == target) and m["user_id"] != desk]
            seen = bool(needed) and all(t in texts for t in needed)
        if not seen:
            missing.append(f"{kind} {target}")
    return missing


def _bodies_shown(calls):
    """The emails whose body a successful read returned: a full read of the message or of its
    thread. A metadata-only or minimal read, or a search snippet, returns no body."""
    shown = set()

    def walk(value):
        if isinstance(value, dict):
            if value.get("id") and "plaintextBody" in value:
                shown.add(value["id"])
            for one in value.values():
                walk(one)
        elif isinstance(value, list):
            for one in value:
                walk(one)

    for c in calls:
        if c.ok and c.name in ("get_thread", "get_message"):
            walk(c.result)
    return shown


def _texts_shown(calls):
    """The Slack messages whose text a successful thread read, channel read or search returned."""
    shown = set()

    def walk(value):
        if isinstance(value, dict):
            if value.get("ts") and "text" in value:
                shown.add(value["ts"])
            for one in value.values():
                walk(one)
        elif isinstance(value, list):
            for one in value:
                walk(one)

    for c in calls:
        if c.ok and c.name in ("slack_read_thread", "slack_read_channel", "slack_search_public_and_private"):
            walk(c.result)
    return shown


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

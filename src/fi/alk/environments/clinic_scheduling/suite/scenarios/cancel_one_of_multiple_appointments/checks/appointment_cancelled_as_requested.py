def check(world, calls, reference=None):
    want = None
    for step in reference or ():
        if step.get("tool") == "cancel_appointment":
            want = step.get("arguments", {}).get("appointment_id") or want
    if not any(c.name == "cancel_appointment" and c.ok for c in calls):
        return "not done: cancel_appointment never succeeded"
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    gone = [a for a in state.get("appointments", []) if a["status"] == "cancelled" and a["cancelled_at"] == now]
    if want and [a["appointment_id"] for a in gone] != [want]:
        return f"done wrong: cancelled {[a['appointment_id'] for a in gone]}, the patient asked to cancel {want}"
    events = {e["event_id"]: e for e in state.get("calendar_events", [])}
    for a in gone:
        if events.get(a["calendar_event_id"], {}).get("status") != "cancelled":
            return f"done wrong: {a['appointment_id']}'s calendar event still blocks the slot"
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was never read"
    return None

def _evidence_missing(world, calls, reference):
    """The evidence the reference fixes (an email thread or message, a provider calendar or event)
    that this run never retrieved by any valid path: a direct read, or a search or slot query that
    listed it."""
    state = world.state()
    thread_of = {m["id"]: m["thread_id"] for m in state.get("gmail_messages", [])}
    cal_of = {e["event_id"]: e["calendar_id"] for e in state.get("calendar_events", [])}
    prov_of_cal = {p["calendar_id"]: p["provider_id"] for p in state.get("providers", [])}
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_thread" and a.get("threadId"):
            wanted.append(("thread", a["threadId"]))
        elif step.get("tool") == "get_message" and a.get("messageId"):
            wanted.append(("thread", thread_of.get(a["messageId"], a["messageId"])))
        elif step.get("tool") == "list_events" and a.get("calendarId"):
            wanted.append(("calendar", a["calendarId"]))
        elif step.get("tool") == "get_event" and a.get("eventId"):
            wanted.append(("calendar", cal_of.get(a["eventId"], a.get("calendarId"))))
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
            elif kind == "calendar" and (
                (c.name == "list_events" and c.arguments.get("calendarId") == target)
                or (c.name == "get_event" and cal_of.get(c.arguments.get("eventId")) == target)
                or (c.name == "find_available_slots" and (
                    c.arguments.get("provider_id") == prov_of_cal.get(target)
                    or any(isinstance(s, dict) and s.get("provider_id") == prov_of_cal.get(target) for s in (c.result or {}).get("slots", []))
                ))
            ):
                seen = True
            if seen:
                break
        if not seen:
            missing.append(f"{kind} {target}")
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

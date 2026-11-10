def check(world, calls, reference=None):
    want = {**_want(reference, "list_customer_appointments"), **_want(reference, "get_appointment")}
    looks = [c for c in calls if c.name in ("get_appointment", "list_customer_appointments") and c.ok]
    if not looks:
        return "not done: the appointment was never read"
    if want.get("appointment_id") and not any(c.arguments.get("appointment_id") == want["appointment_id"] or any(a.get("appointment_id") == want["appointment_id"] for a in (c.result or {}).get("appointments") or []) for c in looks):
        return f"done wrong: {want['appointment_id']} was never read"
    if want.get("customer_id") and not any(c.arguments.get("customer_id") == want["customer_id"] for c in looks):
        return f"done wrong: {want['customer_id']}'s appointments were never read"
    return _blind(world, calls, reference)


def _now(state):
    return (state.get("clock") or [{"now": ""}])[0]["now"]


def _want(reference, tool):
    out = {}
    for step in reference or ():
        if step.get("tool") == tool:
            out.update(step.get("arguments", {}))
    return out


def _evidence_missing(world, calls, reference):
    """The evidence the reference fixes (an email thread or message, a staff calendar) that this run
    never retrieved by any valid path: a direct read, a search that listed it, or for a calendar a
    read of its events or the slots computed from it."""
    state = world.state()
    thread_of = {m["id"]: m["thread_id"] for m in state.get("gmail_messages", [])}
    cal_of_staff = {r["staff_id"]: r["calendar_id"] for r in state.get("staff", [])}
    cal_of_event = {e["event_id"]: e["calendar_id"] for e in state.get("calendar_events", [])}
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
            wanted.append(("calendar", cal_of_event.get(a["eventId"], a.get("calendarId"))))
    missing = []
    for kind, target in wanted:
        seen = False
        for c in calls:
            if not c.ok:
                continue
            r = c.result if isinstance(c.result, dict) else {}
            if kind == "thread" and (
                (c.name == "get_thread" and c.arguments.get("threadId") == target)
                or (c.name == "get_message" and thread_of.get(c.arguments.get("messageId")) == target)
                or (c.name == "search_threads" and any(isinstance(t, dict) and t.get("id") == target for t in r.get("threads") or []))
            ):
                seen = True
            elif kind == "calendar" and (
                (c.name == "list_events" and c.arguments.get("calendarId") == target)
                or (c.name == "get_event" and cal_of_event.get(c.arguments.get("eventId")) == target)
                or (c.name == "list_slots" and any(cal_of_staff.get(s.get("staff_id")) == target for s in r.get("slots") or []))
                or (c.name == "list_slots" and c.arguments.get("staff_id") and cal_of_staff.get(c.arguments.get("staff_id")) == target)
            ):
                seen = True
            if seen:
                break
        if not seen:
            missing.append(f"{kind} {target}")
    return missing


def _blind(world, calls, reference):
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was never read"
    return None


def _stray_events(state):
    now = _now(state)
    appts = {a["calendar_event_id"] for a in state.get("appointments", [])}
    cals = {r["calendar_id"] for r in state.get("staff", [])}
    return [e["event_id"] for e in state.get("calendar_events", []) if e["calendar_id"] in cals and e["created_at"] == now and e["event_id"] not in appts]


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

def check(world, calls, reference=None):
    want = _want(reference, "reschedule_appointment")
    s = world.state()
    now = _now(s)
    if not any(c.name == "reschedule_appointment" and c.ok and (c.result or {}).get("rescheduled") for c in calls):
        return "not done: no reschedule was committed"
    appts = {a["appointment_id"]: a for a in s.get("appointments", [])}
    oid = want.get("appointment_id")
    if oid:
        orig = appts.get(oid)
        if not orig:
            return f"not done: {oid} does not exist"
        if orig["status"] == "cancelled":
            return f"done wrong: {oid} was cancelled instead of moved"
        if orig["status"] != "rescheduled":
            return f"not done: {oid} is still {orig['status']}"
        reps = [a for a in appts.values() if a["rescheduled_from"] == oid and a["status"] == "booked"]
        if len(reps) != 1:
            return f"done wrong: {oid} has {len(reps)} replacements"
        rep = reps[0]
        if want.get("new_start_time") and rep["local_start"] != str(want["new_start_time"])[:16]:
            return f"done wrong: moved to {rep['local_start']}, the customer confirmed {str(want['new_start_time'])[:16]}"
        if want.get("new_staff_id") and rep["staff_id"] != want["new_staff_id"]:
            return f"done wrong: moved to {rep['staff_id']}, the customer asked for {want['new_staff_id']}"
        if want.get("utc_offset") and rep["utc_offset"] != want["utc_offset"]:
            return f"done wrong: moved to UTC{rep['utc_offset']}, the customer chose UTC{want['utc_offset']}"
        events = {e["event_id"]: e for e in s.get("calendar_events", [])}
        if events.get(orig["calendar_event_id"], {}).get("status") != "cancelled" or events.get(rep["calendar_event_id"], {}).get("status") != "confirmed":
            return "done wrong: the calendar does not show the move"
        extra = [a["appointment_id"] for a in appts.values() if a["created_at"] == now and a["source"] == "desk" and a["customer_id"] == orig["customer_id"] and a["appointment_id"] != rep["appointment_id"]]
        if extra:
            return f"done wrong: other appointments were also created: {extra}"
    if _stray_events(s):
        return "done wrong: calendar events without an appointment record"
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

def check(world, calls, reference=None):
    want_appt = want_slot = None
    for step in reference or ():
        if step.get("tool") == "reschedule_appointment":
            want_appt = step.get("arguments", {}).get("appointment_id") or want_appt
            want_slot = step.get("arguments", {}).get("new_slot_id") or want_slot
    if not any(c.name == "reschedule_appointment" and c.ok for c in calls):
        return "not done: reschedule_appointment never succeeded"
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    appts = {a["appointment_id"]: a for a in state.get("appointments", [])}
    moved = [a for a in appts.values() if a["updated_at"] == now and a["reschedule_count"] > 0 and a["status"] == "scheduled"]
    if want_appt:
        a = appts.get(want_appt)
        if not a or a not in moved:
            return f"done wrong: appointment {want_appt} was not moved"
        if want_slot:
            parts = want_slot.split("-")
            start = f"{parts[2][:4]}-{parts[2][4:6]}-{parts[2][6:]}T{parts[3][:2]}:{parts[3][2:]}:00Z"
            if a["provider_id"] != parts[1] or a["start_time"] != start:
                return f"done wrong: {want_appt} is now {a['provider_id']} at {a['start_time']}, the patient confirmed {parts[1]} at {start}"
        events = {e["event_id"]: e for e in state.get("calendar_events", [])}
        e = events.get(a["calendar_event_id"])
        if not e or e["status"] == "cancelled" or e["start_time"] != a["start_time"]:
            return f"done wrong: {want_appt}'s new time is not on the provider's calendar"
        old = [x for x in events.values() if x["description"] == f"appointment_id: {want_appt}" and x["event_id"] != a["calendar_event_id"] and x["status"] != "cancelled"]
        if old:
            return f"done wrong: the original time {old[0]['start_time']} was not released"
        if any(x["created_at"] == now and x["patient_id"] == a["patient_id"] for x in appts.values()):
            return "done wrong: a second appointment was created instead of moving the original"
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

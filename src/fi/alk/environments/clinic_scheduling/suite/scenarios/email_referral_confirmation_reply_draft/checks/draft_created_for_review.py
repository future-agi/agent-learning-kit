def check(world, calls, reference=None):
    import json
    import re
    want_thread = None
    for step in reference or ():
        if step.get("tool") == "get_thread":
            want_thread = step.get("arguments", {}).get("threadId") or want_thread
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    drafts = [d for d in state.get("gmail_drafts", []) if d["created_at"] == now and not d["is_human_edited"]]
    if not drafts:
        return "not done: no draft was created"
    if want_thread and not any(d["thread_id"] == want_thread for d in drafts):
        return f"done wrong: the draft is on {[d['thread_id'] for d in drafts]}, not {want_thread}"
    senders = {m["sender"].lower() for m in state.get("gmail_messages", []) if m["thread_id"] == want_thread and not m["sender"].endswith("@riverbank-clinics.example")}
    for d in drafts:
        if want_thread and d["thread_id"] == want_thread and senders and d["recipient"].lower() not in senders:
            return f"done wrong: the draft is addressed to {d['recipient']}, not the patient who wrote"
    appts = {a["appointment_id"]: a for a in state.get("appointments", [])}
    for d in drafts:
        named = set(re.findall(r"\bA\d{2,3}\b", d["body"]))
        for aid in named:
            if aid not in appts:
                return f"done wrong: the draft names appointment {aid}, which does not exist"
        # The draft is the patient's written record of what the desk did: a cancellation or a move
        # it states must be in the records, and a time it gives must be the appointment's own.
        said = d["body"].lower()
        times = set(re.findall(r"\b\d{2}:\d{2}\b", d["body"]))
        for aid in named:
            a = appts[aid]
            if re.search(r"\bcancell?ed\b", said) and not re.search(r"\bnot\b[^.]*\bcancel", said) and a["status"] != "cancelled":
                return f"done wrong: the draft says {aid} was cancelled, but it is {a['status']}"
            if re.search(r"\b(moved|rescheduled)\b", said) and not (a["reschedule_count"] > 0 and a["updated_at"] == now):
                return f"done wrong: the draft says {aid} was moved, but no move of it was made"
            if times and a["status"] == "scheduled" and a["start_time"][11:16] not in times:
                return f"done wrong: the draft gives {sorted(times)} for {aid}, which is at {a['start_time'][11:16]}"
            if times and not any(c.ok and isinstance(c.result, dict) and aid in json.dumps(c.result) for c in calls
                                 if c.name in ("get_appointment", "list_appointments", "reschedule_appointment", "cancel_appointment", "book_appointment")):
                return f"done blind: the draft gives a time for {aid}, which no lookup in this run returned"
    missing = _evidence_missing(world, calls, reference, "create_draft")
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was not read first"
    return None

def _evidence_missing(world, calls, reference, *writes):
    """The evidence the reference fixes and whether it reached this run: every message of an email
    thread (or the one email) with its body returned by a read, and a provider calendar read directly
    or through the slots computed from it. Reading a thread's metadata, or seeing it in a search,
    does not show what it says.
    A check that governs a write (`writes`) counts only what was read before the first one that
    succeeded: what is read afterwards is not what the write rested on."""
    first = next((i for i, c in enumerate(calls) if c.ok and c.name in writes), len(calls))
    calls = calls[:first]
    state = world.state()
    cal_of = {e["event_id"]: e["calendar_id"] for e in state.get("calendar_events", [])}
    prov_of_cal = {p["calendar_id"]: p["provider_id"] for p in state.get("providers", [])}
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_thread" and a.get("threadId"):
            wanted.append(("email thread", a["threadId"]))
        elif step.get("tool") == "get_message" and a.get("messageId"):
            wanted.append(("email", a["messageId"]))
        elif step.get("tool") == "list_events" and a.get("calendarId"):
            wanted.append(("calendar", a["calendarId"]))
        elif step.get("tool") == "get_event" and a.get("eventId"):
            wanted.append(("calendar", cal_of.get(a["eventId"], a.get("calendarId"))))
    bodies = _bodies_shown(calls)
    missing = []
    for kind, target in wanted:
        if kind in ("email thread", "email"):
            ids = [m["id"] for m in state.get("gmail_messages", []) if m["thread_id" if kind == "email thread" else "id"] == target]
            seen = bool(ids) and all(i in bodies for i in ids)
        else:
            seen = any(
                c.ok and (
                    (c.name == "list_events" and c.arguments.get("calendarId") == target)
                    or (c.name == "get_event" and cal_of.get(c.arguments.get("eventId")) == target)
                    or (c.name == "find_available_slots" and (
                        c.arguments.get("provider_id") == prov_of_cal.get(target)
                        or any(isinstance(s, dict) and s.get("provider_id") == prov_of_cal.get(target) for s in (c.result or {}).get("slots", []))
                    ))
                )
                for c in calls
            )
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

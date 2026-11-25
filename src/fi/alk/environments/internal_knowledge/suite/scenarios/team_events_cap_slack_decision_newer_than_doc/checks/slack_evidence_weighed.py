def check(world, calls, reference=None):
    if not any(c.ok and c.name in ("slack_read_thread", "slack_read_channel", "slack_search_public_and_private") for c in calls):
        return "not done: Slack was never read"
    if not any(c.ok and c.name in ("fetch_passage", "read_file_content", "search_documents", "get_effective_version") for c in calls):
        return "not done: the document was never consulted"
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the answer rests on {', '.join(missing)}, which was never read"
    return _unasked_write(world, reference)


def _evidence_missing(world, calls, reference, *writes):
    """The Slack thread, passage or document the reference fixes and whether its content reached this
    run: the text of every message in the thread (the desk's own posts aside) returned by a thread
    read, channel read or search; a passage's text returned by fetching it or inside its document's
    text read in Drive; a document the reference reads whole, its text, or every one of its passages.
    A reference fixes the decision-bearing passage, so the facts the answer needs are asked for and
    the rest of the document is not. A search returns a snippet, not the passage (unless the snippet is the passage's whole text: see _whole_in_snippet), and another section
    or another version of the document is not that passage.
    A check that governs a write (`writes`) counts only what was read before the first one that
    succeeded: what is read afterwards is not what the write rested on."""
    first = next((i for i, c in enumerate(calls) if c.ok and c.name in writes), len(calls))
    calls = calls[:first]
    state = world.state()
    desk = (state.get("current_actor") or [{}])[0].get("user_id")
    file_of = {p["passage_id"]: p["drive_file_id"] for p in state.get("kb_passages", [])}
    text_of = {p["passage_id"]: p["text"] for p in state.get("kb_passages", [])}
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "slack_read_thread" and a.get("message_ts"):
            wanted.append(("Slack thread", a["message_ts"]))
        elif step.get("tool") == "read_file_content" and a.get("fileId"):
            wanted.append(("document", a["fileId"]))
        elif step.get("tool") == "fetch_passage" and a.get("passage_id"):
            wanted.append(("passage", a["passage_id"]))
    texts = _texts_shown(calls)
    snippets = {}
    for c in calls:
        if c.ok and c.name == "search_documents" and isinstance(c.result, dict):
            for r in c.result.get("results") or []:
                for p in r.get("passages") or []:
                    if p.get("snippet"):
                        snippets.setdefault(p.get("passage_id"), []).append(p["snippet"])
    contents = {}
    fetched = {}
    for c in calls:
        if not c.ok or not isinstance(c.result, dict):
            continue
        if c.name == "read_file_content" and c.result.get("fileContent"):
            contents.setdefault(c.arguments.get("fileId"), []).append(c.result["fileContent"])
        elif c.name == "fetch_passage" and c.result.get("text"):
            fetched[c.result.get("passage_id")] = c.result["text"]
    missing = []
    for kind, target in wanted:
        if kind == "Slack thread":
            needed = [m["ts"] for m in state.get("slack_messages", []) if (m["ts"] == target or m.get("thread_ts") == target) and m["user_id"] != desk]
            seen = bool(needed) and all(t in texts for t in needed)
        elif kind == "document":
            parts = [pid for pid, fid in file_of.items() if fid == target]
            seen = bool(contents.get(target)) or (bool(parts) and all(fetched.get(pid) == text_of[pid] for pid in parts))
        else:
            seen = (target in text_of and fetched.get(target) == text_of[target]) or any(
                text_of.get(target, "\0") in body for body in contents.get(file_of.get(target), [])
            ) or _whole_in_snippet(text_of.get(target), snippets.get(target, []))
        if not seen:
            missing.append(f"{kind} {target}")
    return missing



def _whole_in_snippet(text, snippets):
    """A search snippet stands in for a passage only when it is the passage's whole text (a short
    passage the search returned in full). A cut-off snippet may drop the condition, exception or
    limit the answer turns on, so that passage must be fetched."""
    return bool(text) and any(s == text for s in snippets)

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


def _unasked_write(world, reference):
    """A change the task never asked for: a request, an issue, a request update or a Slack post this
    run made although the reference makes none of that kind. An answer is not an action, and a
    declined offer stays declined."""
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    asked = {step.get("tool") for step in reference or ()}
    actor = (state.get("current_actor") or [{"user_id": ""}])[0]["user_id"]
    posted = {r["message_id"] for r in state.get("slack_message_ledger", []) if r.get("created_at") == now}
    made = {
        "create_knowledge_request": any(r.get("created_at") == now for r in state.get("knowledge_requests", [])),
        "update_knowledge_request": any(r.get("updated_at") == now and r.get("created_at") != now for r in state.get("knowledge_requests", [])),
        "flag_document_issue": any(i.get("created_at") == now for i in state.get("document_issues", [])),
        "slack_send_message": any(m["user_id"] == actor and m["message_id"] in posted for m in state.get("slack_messages", [])),
    }
    what = {"create_knowledge_request": "opened a knowledge request", "update_knowledge_request": "changed a knowledge request", "flag_document_issue": "raised a document issue", "slack_send_message": "posted in Slack"}
    for tool, done in made.items():
        if done and tool not in asked:
            return f"done wrong: the run {what[tool]}, which nobody asked for"
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

def check(world, calls, reference=None):
    if not any(c.ok and c.name in ("slack_read_thread", "slack_read_channel", "slack_search_public_and_private") for c in calls):
        return "not done: Slack was never read"
    if not any(c.ok and c.name in ("fetch_passage", "read_file_content", "search_documents", "get_effective_version") for c in calls):
        return "not done: the document was never consulted"
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the answer rests on {', '.join(missing)}, which was never read"
    return None

def _evidence_missing(world, calls, reference):
    """The Slack thread, document or passage the reference fixes and whether its content reached this
    run: the text of every message in the thread (the desk's own posts aside) returned by a thread
    read, channel read or search; the document read whole or through one of its passages; the passage
    fetched or read in its document. A search returns a snippet, not the passage."""
    state = world.state()
    desk = (state.get("current_actor") or [{}])[0].get("user_id")
    file_of = {p["passage_id"]: p["drive_file_id"] for p in state.get("kb_passages", [])}
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
    missing = []
    for kind, target in wanted:
        if kind == "Slack thread":
            needed = [m["ts"] for m in state.get("slack_messages", []) if (m["ts"] == target or m.get("thread_ts") == target) and m["user_id"] != desk]
            seen = bool(needed) and all(t in texts for t in needed)
        else:
            seen = False
            for c in calls:
                if not c.ok:
                    continue
                if kind == "document":
                    seen = (c.name == "read_file_content" and c.arguments.get("fileId") == target) or (
                        c.name == "fetch_passage" and file_of.get(c.arguments.get("passage_id")) == target
                    )
                else:
                    seen = (c.name == "fetch_passage" and c.arguments.get("passage_id") == target) or (
                        c.name == "read_file_content" and c.arguments.get("fileId") == file_of.get(target)
                    )
                if seen:
                    break
        if not seen:
            missing.append(f"{kind} {target}")
    return missing


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

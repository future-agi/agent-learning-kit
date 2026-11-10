def check(world, calls, reference=None):
    reads = [c for c in calls if c.ok and c.name in ("search_documents", "fetch_passage", "read_file_content", "get_effective_version", "get_document_metadata")]
    if not reads:
        return "not done: no document was consulted"
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the answer rests on {', '.join(missing)}, which was never read"
    return None

def _evidence_missing(world, calls, reference):
    """The Slack thread, document or passage the reference fixes, and whether the run retrieved it by
    any valid path: a direct read, a passage of the document, the whole document holding the passage,
    a search that listed the passage, or a channel read or search that listed the message."""
    state = world.state()
    file_of = {p["passage_id"]: p["drive_file_id"] for p in state.get("kb_passages", [])}
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "slack_read_thread" and a.get("message_ts"):
            wanted.append(("thread", a["message_ts"]))
        elif step.get("tool") == "read_file_content" and a.get("fileId"):
            wanted.append(("document", a["fileId"]))
        elif step.get("tool") == "fetch_passage" and a.get("passage_id"):
            wanted.append(("passage", a["passage_id"]))
    missing = []
    for kind, target in wanted:
        seen = False
        for c in calls:
            if not c.ok:
                continue
            r = c.result if isinstance(c.result, dict) else {}
            if kind == "thread":
                if c.name == "slack_read_thread" and c.arguments.get("message_ts") == target:
                    seen = True
                elif c.name in ("slack_read_channel", "slack_search_public_and_private") and any(isinstance(m, dict) and (m.get("ts") == target or m.get("thread_ts") == target) for m in r.get("messages") or []):
                    seen = True
            elif kind == "document":
                if c.name == "read_file_content" and c.arguments.get("fileId") == target:
                    seen = True
                elif c.name == "fetch_passage" and file_of.get(c.arguments.get("passage_id")) == target:
                    seen = True
            else:
                if c.name == "fetch_passage" and c.arguments.get("passage_id") == target:
                    seen = True
                elif c.name == "read_file_content" and c.arguments.get("fileId") == file_of.get(target):
                    seen = True
                elif c.name == "search_documents" and any(isinstance(p, dict) and p.get("passage_id") == target for res in r.get("results") or [] for p in res.get("passages") or []):
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

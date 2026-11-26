def check(world, calls, reference=None):
    fid = None
    for step in reference or ():
        if step.get("tool") in ("get_file_permissions", "get_file_metadata") and step.get("arguments", {}).get("fileId"):
            fid = step["arguments"]["fileId"]
    if not fid:
        return "not done: the scenario fixes no document whose owner decides the change"
    read = any(
        c.name == "read_file_content" and c.ok and c.arguments.get("fileId") == fid
        and isinstance(c.result, dict) and c.result.get("fileContent")
        for c in calls
    )
    if not read:
        return f"not done: {fid}'s text, the rule the change is about, was never read"
    # The desk has no Drive write tool, so routing is the outcome: who edits the document must
    # have reached the run, through its permissions or its metadata.
    owners = any(
        c.name in ("get_file_permissions", "get_file_metadata") and c.ok and c.arguments.get("fileId") == fid
        and isinstance(c.result, dict) and c.result
        for c in calls
    )
    if not owners:
        return f"not done: who may edit {fid} was never looked up, so the change has nowhere to go"
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
    # Only the verdict: a failure names the records it read, so it is not written to the console.
    print("held" if _held else "FAILED")
    raise SystemExit(0 if _held else 1)

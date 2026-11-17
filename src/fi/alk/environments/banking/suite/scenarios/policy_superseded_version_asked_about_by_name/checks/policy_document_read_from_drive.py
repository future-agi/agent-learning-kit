def check(world, calls, reference=None):
    wanted = []
    for step in reference or ():
        if step.get("tool") == "read_file_content" and step.get("arguments", {}).get("fileId"):
            wanted.append(step["arguments"]["fileId"])
    reads = [c for c in calls if c.name == "read_file_content" and c.ok]
    if not reads:
        return "not done: no policy document was read from Drive"
    files = {f["file_id"]: f for f in world.state().get("drive_files", [])}
    for file_id in wanted:
        if not any(c.arguments.get("fileId") == file_id for c in reads):
            return f"done wrong: {file_id} ({files.get(file_id, {}).get('title', 'unknown')}) was never read"
    for c in reads:
        f = files.get(c.arguments.get("fileId"))
        if f is None:
            return f"done wrong: read of a file that does not exist: {c.arguments.get('fileId')}"
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

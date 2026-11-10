def check(world, calls, reference=None):
    fid = None
    for step in reference or ():
        if step.get("tool") == "get_document_owner":
            fid = step.get("arguments", {}).get("drive_file_id") or fid
    # The owner tool, the team directory, or any metadata read that returns the document's owner (plan §3.3).
    looks = [
        c for c in calls
        if c.ok and (
            c.name in ("get_document_owner", "get_team_directory")
            or (c.name in ("check_document_access", "get_document_metadata") and "owner_id" in str(c.result))
            or (c.name == "get_file_metadata" and "owner" in str(c.result).lower())
        )
    ]
    if not looks:
        return "not done: no owner was looked up"
    if fid and not any(c.name == "get_team_directory" or fid in (c.arguments.get("drive_file_id"), c.arguments.get("fileId")) for c in looks):
        return f"done wrong: the owner of {fid} was never looked up"
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

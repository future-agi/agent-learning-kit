def check(world, calls, reference=None):
    made_calls = [c for c in calls if c.name == "label_thread" and c.ok]
    if not made_calls:
        return "not done: no successful label_thread call was made"
        
    wanted_thread = None
    wanted_labels = None
    if reference:
        for step in reference:
            if step.get("tool") == "label_thread" and "thread_id" in step.get("arguments", {}):
                wanted_thread = step["arguments"]["thread_id"]
                if "add_label_ids" in step.get("arguments", {}):
                    wanted_labels = step["arguments"]["add_label_ids"]
                    
    tl = world.state().get("thread_labels", [])
    
    for c in made_calls:
        t_id = c.arguments.get("thread_id")
        if wanted_thread and t_id != wanted_thread:
            return f"done wrong: label_thread called for thread {t_id!r}, expected {wanted_thread!r}"
            
        added = c.arguments.get("add_label_ids") or []
        if isinstance(added, str):
            added = [added]
        if not added:
            return "not done: label_thread called without add_label_ids"
            
        if wanted_labels:
            w_list = [wanted_labels] if isinstance(wanted_labels, str) else wanted_labels
            for lid in w_list:
                if lid not in added:
                    return f"done wrong: label {lid!r} was not added to thread {t_id!r}"
                    
        for lid in added:
            if not any(row["thread_id"] == t_id and row["label_id"] == lid for row in tl):
                return f"not done: label {lid!r} is not present in thread_labels for thread {t_id!r}"
                
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

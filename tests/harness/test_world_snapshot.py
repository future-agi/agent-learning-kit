"""The world's records before and after a trial, and whether the trial started from the seed."""

from __future__ import annotations

from fi.alk.harness.call_runner import TrialRecords
from fi.alk.harness.world_snapshot import diff, digest, from_rows, start_state


def _orders(*rows):
    return {"orders": (["id"], [dict(row) for row in rows]), "refunds": (["id"], [])}


def test_diff_names_added_changed_and_removed_rows():
    before = from_rows({"orders": (["id"], [{"id": 1, "status": "paid"}, {"id": 2, "status": "paid"}])})
    after = from_rows(
        {"orders": (["id"], [{"id": 1, "status": "refunded"}, {"id": 3, "status": "paid"}])}
    )
    changes = {(one["key"][0], one["kind"]): one for one in diff(before, after)["changes"]}
    assert set(changes) == {(1, "changed"), (2, "removed"), (3, "added")}
    assert changes[(1, "changed")]["fields"] == ["status"]
    assert changes[(1, "changed")]["before"] == {"id": 1, "status": "paid"}
    assert changes[(1, "changed")]["after"] == {"id": 1, "status": "refunded"}


def test_internal_tables_are_never_compared():
    snap = from_rows({"_alk_tool_trace": (["id"], [{"id": 1}]), "orders": (["id"], [])})
    assert set(snap.tables) == {"orders"}


def test_a_table_without_a_primary_key_is_skipped_and_named():
    before = from_rows({"audit": ([], [{"line": "a"}]), "orders": (["id"], [])})
    after = from_rows({"audit": ([], [{"line": "a"}, {"line": "b"}]), "orders": (["id"], [])})
    found = diff(before, after)
    assert found["changes"] == []
    assert found["skipped"] == [{"table": "audit", "why": "no primary key"}]


def test_a_table_over_the_row_cap_is_skipped_and_named():
    snap = from_rows({"events": (["id"], [{"id": n} for n in range(6)])}, max_rows=5)
    assert snap.tables == {} and snap.skipped == [{"table": "events", "why": "over 5 rows"}]


def test_changes_over_the_cap_are_counted_not_dropped_silently():
    before = from_rows({"orders": (["id"], [])})
    after = from_rows({"orders": (["id"], [{"id": n} for n in range(5)])})
    found = diff(before, after, max_changes=2)
    assert len(found["changes"]) == 2 and found["truncated"] == 3


def test_a_second_trial_that_starts_from_changed_rows_reads_differs(monkeypatch):
    import fi.alk.harness.world_snapshot as ws

    starts = iter(
        [
            from_rows(_orders({"id": 1, "status": "paid"})),
            from_rows(_orders({"id": 1, "status": "paid"})),
            from_rows(_orders({"id": 1, "status": "refunded"})),
        ]
    )
    monkeypatch.setattr(ws, "snapshot", lambda dsn, **_: next(starts))
    records = TrialRecords()
    records.start("w0", "dsn")
    records._starts.pop("w0")
    records.start("w0", "dsn")
    assert records._starts.pop("w0")[1] == {"state": "seeded", "tables": []}
    # The third start is compared with the seed, not with the trial before it.
    records.start("w0", "dsn")
    assert records._starts["w0"][1] == {"state": "differs", "tables": ["orders"]}


def test_start_state_is_unknown_when_a_start_could_not_be_read():
    assert start_state(None, {"orders": "x"})["state"] == "unknown"
    snap = from_rows(_orders({"id": 1}))
    assert start_state(digest(snap), digest(snap)) == {"state": "seeded", "tables": []}


def test_a_trial_whose_records_cannot_be_read_is_sealed_unreadable_never_failed(monkeypatch):
    import fi.alk.harness.world_snapshot as ws

    def broken(dsn, **_):
        raise RuntimeError("down")

    monkeypatch.setattr(ws, "snapshot", broken)
    records = TrialRecords()
    records.start("w0", "dsn")
    assert records.end("w0", "dsn") == {"records": {"unreadable": True}, "start": {"state": "unknown", "tables": []}}


def test_world_changes_carry_the_rows_the_trial_changed(monkeypatch):
    import fi.alk.harness.world_snapshot as ws

    taken = iter([from_rows(_orders({"id": 1042, "status": "paid"})),
                  from_rows({"orders": (["id"], [{"id": 1042, "status": "refunded"}]),
                             "refunds": (["id"], [{"id": 1, "order_id": 1042}])})])
    monkeypatch.setattr(ws, "snapshot", lambda dsn, **_: next(taken))
    records = TrialRecords()
    records.start("w0", "dsn")
    sealed = records.end("w0", "dsn")
    kinds = sorted((one["table"], one["kind"]) for one in sealed["records"]["changes"])
    assert kinds == [("orders", "changed"), ("refunds", "added")]
    assert sealed["start"]["state"] == "seeded"


def test_the_trial_s_end_state_is_stored_as_its_own_artifact_with_the_action_log(monkeypatch):
    import asyncio
    import json

    import fi.alk.harness.world_snapshot as ws
    from fi.alk.harness.call_runner import END_STATE_KEY, upload_end_state
    from fi.alk.harness.outbound import ArtifactKind

    taken = iter([from_rows(_orders({"id": 1, "status": "paid"})), from_rows(_orders({"id": 1, "status": "refunded"}))])
    monkeypatch.setattr(ws, "snapshot", lambda dsn, **_: next(taken))
    records = TrialRecords()
    records.start("w0", "dsn")
    sealed = records.end("w0", "dsn")
    uploads = []

    class _Adapter:
        async def upload_artifact(self, data, *, kind, scenario_key):
            uploads.append((kind, scenario_key, json.loads(data)))
            return "art-1"

    log = [{"name": "issue_refund", "actor_id": "agent", "outcome": "committed"}]
    assert asyncio.run(upload_end_state(_Adapter(), "k1", sealed, log)) == "art-1"
    [(kind, key, body)] = uploads
    assert (kind, key, body["schema"]) == (ArtifactKind.WORLD_STATE, "k1", "futureagi.world-state.v1")
    assert body["tables"]["orders"] == [{"id": 1, "status": "refunded"}] and body["action_log"] == log
    assert END_STATE_KEY not in sealed and "records" in sealed


def test_no_end_state_is_stored_when_the_end_could_not_be_read():
    import asyncio

    from fi.alk.harness.call_runner import upload_end_state

    class _Adapter:
        async def upload_artifact(self, data, *, kind, scenario_key):
            raise AssertionError("nothing to store")

    assert asyncio.run(upload_end_state(_Adapter(), "k1", {"records": {"unreadable": True}}, [])) is None


def test_a_huge_field_is_cut_in_the_change_but_still_counts_as_changed():
    from fi.alk.harness.world_snapshot import MAX_VALUE_CHARS, diff, from_rows

    big = "x" * (MAX_VALUE_CHARS * 50)
    before = from_rows({"docs": (["id"], [{"id": 1, "body": big, "notes": {"k": big}}])})
    after = from_rows({"docs": (["id"], [{"id": 1, "body": big + "y", "notes": {"k": big}}])})

    [change] = diff(before, after)["changes"]
    assert change["fields"] == ["body"]
    assert len(change["after"]["body"]) < MAX_VALUE_CHARS + 50
    assert change["after"]["body"].endswith(f"({len(big) + 1 - MAX_VALUE_CHARS} more characters)")
    assert len(change["before"]["notes"]) < MAX_VALUE_CHARS + 50
    assert change["after"]["id"] == 1


def test_pre_run_sessions_neither_make_a_trial_read_differs_nor_count_as_its_changes(monkeypatch):
    import fi.alk.harness.world_snapshot as ws

    seed = from_rows({"orders": (["id"], [{"id": 1, "status": "paid"}]), "messages": (["id"], [])})
    pre_run = from_rows(
        {"orders": (["id"], [{"id": 1, "status": "paid"}]), "messages": (["id"], [{"id": 9, "body": "hi"}])}
    )
    end = from_rows(
        {"orders": (["id"], [{"id": 1, "status": "refunded"}]), "messages": (["id"], [{"id": 9, "body": "hi"}])}
    )
    taken = iter([seed, seed, pre_run, end])
    monkeypatch.setattr(ws, "snapshot", lambda dsn, **_: next(taken))
    records = TrialRecords()
    # The first trial set the seed; the second wrote a pre-run session's message before it started.
    records.start("w0", "dsn")
    records._starts.pop("w0")
    records.start("w0", "dsn")
    records.rebase("w0", "dsn")
    sealed = records.end("w0", "dsn")
    assert sealed["start"] == {"state": "seeded", "tables": []}
    assert [(one["table"], one["kind"]) for one in sealed["records"]["changes"]] == [("orders", "changed")]

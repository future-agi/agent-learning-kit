"""The world's records before and after a trial, and whether the trial started from the seed."""

from __future__ import annotations

import os

import pytest

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
    assert records._starts.pop("w0")[1] == {"state": "seeded", "tables": [], "against": "first_trial"}
    # The third start is compared with the seed, not with the trial before it.
    records.start("w0", "dsn")
    assert records._starts["w0"][1] == {"state": "differs", "tables": ["orders"], "against": "first_trial"}


def test_every_trial_s_start_is_compared_with_the_sealed_seed_never_with_trial_one(monkeypatch):
    import fi.alk.harness.world_snapshot as ws

    two = from_rows({"orders": (["id"], [{"id": 1}, {"id": 2}]), "refunds": (["id"], [])})
    taken = iter([two, from_rows(_orders({"id": 1}))])
    read: list[str] = []
    monkeypatch.setattr(ws, "snapshot", lambda dsn, **_: read.append(dsn) or next(taken))
    # The seed as the build digested it at seal time: one order.
    records = TrialRecords(lambda: digest(from_rows(_orders({"id": 1}))))
    # Trial 1 started with an extra row: it differs from the sealed seed, and it is no one's baseline.
    records.start("w0", "dsn")
    assert records._starts.pop("w0")[1] == {"state": "differs", "tables": ["orders"], "against": "sealed_seed"}
    records.start("w0", "dsn")
    assert records._starts.pop("w0")[1] == {"state": "seeded", "tables": [], "against": "sealed_seed"}
    # Only the world's own database is ever read: never the template every world is reset from.
    assert read == ["dsn", "dsn"]


@pytest.mark.skipif(not os.environ.get("ALK_TEST_POSTGRES_DSN"), reason="needs ALK_TEST_POSTGRES_DSN")
def test_a_seed_sealed_closed_to_connections_is_still_what_every_trial_s_start_is_compared_with(tmp_path):
    import json
    import uuid

    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from fi.alk.harness.call_runner import sealed_seed
    from fi.alk.harness.process_runtime import EngineCredentials, sealed_seed_digest

    admin = os.environ["ALK_TEST_POSTGRES_DSN"]
    parts = conninfo_to_dict(admin)
    template, world = f"seed_{uuid.uuid4().hex[:8]}", f"world_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{template}"')
    try:
        with psycopg.connect(make_conninfo(admin, dbname=template), autocommit=True) as connection:
            connection.execute("CREATE TABLE orders (id int PRIMARY KEY, status text)")
            connection.execute("INSERT INTO orders VALUES (1, 'open')")
        # The seal path: digest first, then the template closes, as the build does.
        digested = sealed_seed_digest(
            host=parts["host"], port=int(parts.get("port") or 5432),
            credentials=EngineCredentials(parts["user"], parts.get("password", "")), dbname=template,
        )
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(f'ALTER DATABASE "{template}" WITH IS_TEMPLATE true ALLOW_CONNECTIONS false')
            connection.execute(f'CREATE DATABASE "{world}" TEMPLATE "{template}"')
        (tmp_path / "artifacts").mkdir()
        (tmp_path / "artifacts" / "build.json").write_text(
            json.dumps({"stores": [{"strategy": "template_database", "baseline_reference": template,
                                    "seed_digest": digested}]})
        )
        world_dsn = make_conninfo(admin, dbname=world)
        records = TrialRecords(sealed_seed(tmp_path))
        records.start("t1", world_dsn)
        assert records._starts.pop("t1")[1] == {"state": "seeded", "tables": [], "against": "sealed_seed"}
        with psycopg.connect(world_dsn, autocommit=True) as connection:
            connection.execute("UPDATE orders SET status = 'closed'")
        records.start("t2", world_dsn)
        assert records._starts.pop("t2")[1] == {"state": "differs", "tables": ["orders"], "against": "sealed_seed"}
    finally:
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{world}"')
            connection.execute(f'ALTER DATABASE "{template}" WITH IS_TEMPLATE false')
            connection.execute(f'DROP DATABASE IF EXISTS "{template}"')


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
    assert records.end("w0", "dsn") == {
        "records": {"unreadable": True}, "start": {"state": "unknown", "tables": [], "against": "first_trial"}
    }


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
    assert sealed["start"] == {"state": "seeded", "tables": [], "against": "first_trial"}
    assert [(one["table"], one["kind"]) for one in sealed["records"]["changes"]] == [("orders", "changed")]


@pytest.mark.skipif(not os.environ.get("ALK_TEST_POSTGRES_DSN"), reason="needs ALK_TEST_POSTGRES_DSN")
def test_each_trial_of_a_run_starts_from_the_seed_and_its_setup_s_key_never_blocks_a_generated_one(tmp_path):
    import json
    import uuid

    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from fi.alk.harness.call_runner import sealed_seed
    from fi.alk.harness.process_runtime import (
        EngineCredentials,
        _reset_template_database,
        default_sql_runner,
        sealed_seed_digest,
    )
    from fi.alk.harness.world.stores.postgres import AttachedPostgresStore

    admin = os.environ["ALK_TEST_POSTGRES_DSN"]
    parts = conninfo_to_dict(admin)
    port = int(parts.get("port") or 5432)
    credentials = EngineCredentials(parts["user"], parts.get("password", ""))
    template, world = f"seed_{uuid.uuid4().hex[:8]}", f"w_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{template}"')
    try:
        with psycopg.connect(make_conninfo(admin, dbname=template), autocommit=True) as connection:
            connection.execute("CREATE TABLE policies (policy_id text PRIMARY KEY, status text)")
            connection.execute("INSERT INTO policies VALUES ('pol_1', 'active')")
            connection.execute(
                "CREATE TABLE approvals (id SERIAL PRIMARY KEY, policy_id text REFERENCES policies, status text)"
            )
        digested = sealed_seed_digest(host=parts["host"], port=port, credentials=credentials, dbname=template)
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(f'ALTER DATABASE "{template}" WITH IS_TEMPLATE true ALLOW_CONNECTIONS false')
        (tmp_path / "artifacts").mkdir()
        (tmp_path / "artifacts" / "build.json").write_text(
            json.dumps({"stores": [{"strategy": "template_database", "seed_digest": digested}]})
        )
        world_dsn = make_conninfo(admin, dbname=world)
        store = AttachedPostgresStore(world_dsn)
        records = TrialRecords(sealed_seed(tmp_path))

        def reset() -> None:
            _reset_template_database(
                host=parts["host"], port=port, credentials=credentials, world_db=world, template_db=template,
                sql_runner=default_sql_runner, process_name="world-db",
            )

        # A table's columns read from the database's own schema, as an actor's fallback takes them.
        reset()
        assert sorted(store.columns("approvals")) == ["id", "policy_id", "status"]

        # Trial 1: its scenario's setup puts a decided approval under an explicit key, then an actor's approval
        # takes its key from the table's own sequence.
        reset()
        records.before_setup("w0", world_dsn)
        store.add("approvals", {"id": 1, "policy_id": "pol_1", "status": "declined"})
        records.begin("w0", world_dsn)
        written = store.add("approvals", {"policy_id": "pol_1", "status": "approved"})
        first = records.end("w0", world_dsn)
        assert first["start"] == {"state": "seeded", "tables": [], "against": "sealed_seed"}
        assert [(one["kind"], one["key"]) for one in first["records"]["changes"]] == [("added", [written["id"]])]

        # Trial 2 on the same world: none of trial 1's records are there, and it starts from the seed.
        reset()
        records.before_setup("w0", world_dsn)
        assert store.state()["approvals"] == []
        records.begin("w0", world_dsn)
        second = records.end("w0", world_dsn)
        assert second["start"] == {"state": "seeded", "tables": [], "against": "sealed_seed"}
        assert second["records"]["changes"] == []

        # Trial 3, a hosted chat: its setup runs here, its call in a worker process with records of its own.
        reset()
        records.before_setup("w0", world_dsn)
        store.add("approvals", {"id": 1, "policy_id": "pol_1", "status": "declined"})
        worker = TrialRecords(sealed_seed(tmp_path))
        worker.started("chat", records.start_of("w0"))
        worker.begin("chat", world_dsn)
        third = worker.end("chat", world_dsn)
        assert third["start"] == {"state": "seeded", "tables": [], "against": "sealed_seed"}
        assert third["records"]["changes"] == []
    finally:
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{world}" WITH (FORCE)')
            connection.execute(f'ALTER DATABASE "{template}" WITH IS_TEMPLATE false')
            connection.execute(f'DROP DATABASE IF EXISTS "{template}"')

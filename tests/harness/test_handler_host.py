"""Environment-authored handlers run in an isolated child process (ALK_HANDLER_ISOLATION).

These run the child without a user namespace ("process"), which every host supports; the
namespace itself is what the hosted image adds on top, and is exercised in a sandbox.
"""

from __future__ import annotations

import datetime
import decimal
import sqlite3
import threading
import types

import pytest

from fi.alk.harness.world import handler_host
from fi.alk.harness.world.handler_host import (
    HandlerHostError,
    HandlerTimeout,
    run_handler,
)
from fi.alk.harness.world.runtime import Db, GeneratedWorld, ToolError


def _world(handlers: dict[str, str], *, isolated: bool) -> GeneratedWorld:
    world = GeneratedWorld(":memory:", kind="sqlite")
    world.store.apply(
        "CREATE TABLE orders (order_id TEXT PRIMARY KEY, total REAL, status TEXT);"
        "INSERT INTO orders VALUES ('O1', 30.5, 'open');"
    )
    world.handlers = handlers
    world.isolated = isolated
    return world


HANDLERS = {
    "get_order": (
        "def handle(args, db):\n"
        "    order = db.one('SELECT * FROM orders WHERE order_id = ?', (args.get('order_id'),))\n"
        "    if order is None:\n"
        "        raise ToolError(f\"No order {args.get('order_id')!r}.\", category='not_found')\n"
        "    return order\n"
    ),
    "close_order": (
        "def handle(args, db):\n"
        "    changed = db.execute(\"UPDATE orders SET status = 'closed' WHERE order_id = ?\","
        " (args['order_id'],))\n"
        "    return {'changed': changed}\n"
    ),
    "own_refusal": (
        "class ToolError(Exception):\n"
        "    pass\n"
        "def handle(args, db):\n"
        "    raise ToolError('declared its own refusal')\n"
    ),
    "broken": "def handle(args, db):\n    return 1 / 0\n",
    "snoop": (
        "import collections\n"
        "def handle(args, db):\n"
        "    import os\n"
        "    return {\n"
        "        'direct': os.environ.get('BUILDER_REVIEW_CANARY'),\n"
        "        'via_collections': collections._sys.modules['os'].environ.get(\n"
        "            'BUILDER_REVIEW_CANARY'\n"
        "        ),\n"
        "    }\n"
    ),
}


def _outcome(world: GeneratedWorld, name: str, arguments: dict) -> tuple:
    call = world.call(name, arguments)
    return call.ok, call.refused, call.error, call.result


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_order", {"order_id": "O1"}),
        ("get_order", {"order_id": "nope"}),
        ("close_order", {"order_id": "O1"}),
        ("own_refusal", {}),
        ("broken", {}),
    ],
)
def test_an_isolated_handler_answers_exactly_as_in_process(
    monkeypatch, name, arguments
):
    in_process = _world(HANDLERS, isolated=False)
    expected = _outcome(in_process, name, arguments)
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    isolated = _world(HANDLERS, isolated=True)

    assert _outcome(isolated, name, arguments) == expected
    # The child's writes land in the world the call was made on.
    assert isolated.store.query("SELECT * FROM orders") == in_process.store.query(
        "SELECT * FROM orders"
    )


def test_an_isolated_handler_cannot_read_the_builders_environment(monkeypatch):
    monkeypatch.setenv("BUILDER_REVIEW_CANARY", "canary-value")
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    world = _world(HANDLERS, isolated=True)

    ok, _refused, error, result = _outcome(world, "snoop", {})

    assert ok, error
    assert result == {"direct": None, "via_collections": None}


def test_an_isolated_handler_cannot_attach_a_host_sqlite_file(monkeypatch, tmp_path):
    secret = tmp_path / "secret.sqlite"
    with sqlite3.connect(secret) as connection:
        connection.execute("CREATE TABLE secrets (value TEXT)")
        connection.execute("INSERT INTO secrets VALUES ('host-secret')")
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    world = _world(
        {
            "read_host": (
                "def handle(args, db):\n"
                "    db.execute('ATTACH DATABASE ? AS host', (args['path'],))\n"
                "    return db.query('SELECT value FROM host.secrets')\n"
            )
        },
        isolated=True,
    )

    call = world.call("read_host", {"path": str(secret)})

    assert not call.ok
    assert call.result is None


def test_an_isolated_handler_cannot_write_a_host_sqlite_file(monkeypatch, tmp_path):
    exported = tmp_path / "exported.sqlite"
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    world = _world(
        {
            "write_host": (
                "def handle(args, db):\n"
                "    db.execute('VACUUM INTO ?', (args['path'],))\n"
                "    return {'written': True}\n"
            )
        },
        isolated=True,
    )

    call = world.call("write_host", {"path": str(exported)})

    assert not call.ok
    assert not exported.exists()


def test_an_isolated_handler_cannot_run_an_unlisted_sqlite_pragma(monkeypatch):
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    world = _world(
        {
            "change_journal": (
                "def handle(args, db):\n    return db.query('PRAGMA journal_mode')\n"
            )
        },
        isolated=True,
    )

    call = world.call("change_journal", {})

    assert not call.ok


def test_a_refusal_keeps_its_category():
    world = _world({}, isolated=True)
    with pytest.raises(ToolError) as refused:
        run_handler(
            HANDLERS["get_order"],
            "get_order",
            {"order_id": "nope"},
            Db(world.store),
            mode="process",
        )
    assert refused.value.category == "not_found"
    assert str(refused.value) == "not_found: No order 'nope'."


class _TypedStore:
    """A store whose rows carry the types PostgreSQL hands back."""

    def query(self, sql, params=()):
        return [
            {
                "amount": decimal.Decimal("84.20"),
                "posted": datetime.datetime(2026, 3, 2, 9, 0, tzinfo=datetime.UTC),
                "day": datetime.date(2026, 3, 2),
                "raw": b"\x00\x01",
            }
        ]

    def execute(self, sql, params=()):
        return 0


def test_values_keep_their_types_both_ways():
    source = (
        "def handle(args, db):\n"
        "    row = db.one('SELECT 1')\n"
        "    return {'row': row, 'doubled': row['amount'] * 2}\n"
    )

    value = run_handler(source, "typed", {}, Db(_TypedStore()), mode="process")

    assert value == {
        "row": _TypedStore().query("")[0],
        "doubled": decimal.Decimal("168.40"),
    }


def test_a_hung_handler_times_out_and_the_next_call_still_runs():
    world = _world({}, isolated=True)
    with pytest.raises(HandlerTimeout):
        run_handler(
            "def handle(args, db):\n    while True:\n        pass\n",
            "spin",
            {},
            Db(world.store),
            mode="process",
            timeout=1.0,
        )

    value = run_handler(
        HANDLERS["get_order"],
        "get_order",
        {"order_id": "O1"},
        Db(world.store),
        mode="process",
    )
    assert value["status"] == "open"


def _run(source: str, **extra):
    return run_handler(source, "t", {}, None, mode="process", **extra)


ONE = "def handle(args, db):\n    return 1\n"


@pytest.fixture
def started(monkeypatch):
    """Every child started in the test, and an empty pool before and after it."""
    handler_host._close_all()
    children = []

    class Child(handler_host._Child):
        def __init__(self, mode):
            super().__init__(mode)
            children.append(self)

    monkeypatch.setattr(handler_host, "_Child", Child)
    yield children
    handler_host._close_all()


def _alive(children) -> int:
    return sum(child.process.poll() is None for child in children)


def test_a_thread_per_call_shares_the_pool_instead_of_keeping_a_child_each(started):
    for _ in range(6):
        thread = threading.Thread(target=_run, args=(ONE,))
        thread.start()
        thread.join()

    assert len(started) == 1
    # More at once than the pool keeps: each gets a child, and only the pool's share outlives them.
    barrier = threading.Barrier(handler_host._MOST_IDLE + 3)
    threads = [
        threading.Thread(
            target=lambda: run_handler(
                "def handle(args, db):\n    return db.one('SELECT 1')\n",
                "t",
                {},
                types.SimpleNamespace(one=lambda *_args: barrier.wait(20)),
                mode="process",
            )
        )
        for _ in range(barrier.parties)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(started) == barrier.parties
    assert _alive(started) == handler_host._MOST_IDLE


def test_a_handler_that_prints_answers_and_its_stderr_never_blocks_it(started):
    noisy = (
        "import sys\n"
        "def handle(args, db):\n"
        "    print('diagnostic' * 1000)\n"
        "    sys.stderr.write('x' * 1000)\n"
        "    return 1\n"
    )

    # Together far more than a pipe holds.
    assert [_run(noisy, timeout=10) for _ in range(200)] == [1] * 200
    assert len(started) == 1
    assert len(started[0].stderr) <= handler_host._STDERR_TAIL_BYTES


def test_a_child_that_breaks_the_protocol_is_an_infrastructure_fault(started):
    with pytest.raises(HandlerHostError, match="malformed"):
        _run(
            "import os\n"
            "def handle(args, db):\n"
            "    os.write(1, b'not json\\n')\n"
            "    return 1\n"
        )
    with pytest.raises(HandlerHostError, match="last words"):
        _run(
            "import os\n"
            "def handle(args, db):\n"
            "    os.write(2, b'last words')\n"
            "    os._exit(3)\n"
        )

    assert _alive(started) == 0
    assert _run(ONE) == 1


def test_the_mapped_ids_come_from_the_callers_own_range(monkeypatch, tmp_path):
    subuid, subgid = tmp_path / "subuid", tmp_path / "subgid"
    subuid.write_text("svc-control:100000:65536\nsvc-tools:524288:1\n")
    subgid.write_text("svc-control:200000:65536\n2002:265536:1\n")
    monkeypatch.setattr(handler_host, "_SUBUID", subuid)
    monkeypatch.setattr(handler_host, "_SUBGID", subgid)
    users = {2000: "svc-control", 2001: "svc-agent", 2002: "svc-tools"}
    monkeypatch.setattr(
        handler_host.pwd,
        "getpwuid",
        lambda uid: types.SimpleNamespace(pw_name=users[uid]),
    )

    def mapped(uid: int) -> list[str]:
        monkeypatch.setattr(handler_host.os, "getuid", lambda: uid)
        return [
            one for one in handler_host._command("namespace") if one.startswith("--map-")
        ]

    assert mapped(2000) == ["--map-users=0:100000:1", "--map-groups=0:200000:1"]
    assert mapped(2002) == ["--map-users=0:524288:1", "--map-groups=0:265536:1"]
    with pytest.raises(HandlerHostError, match="no range"):
        mapped(2001)


def test_the_hosted_image_gives_every_user_that_runs_handlers_a_range_of_its_own():
    from pathlib import Path

    from fi.alk.harness.bundle_v2 import ProcessUser

    image = (Path(__file__).parents[2] / "Dockerfile.hosted").read_text()
    for name in ("subuid", "subgid"):
        (line,) = [one for one in image.splitlines() if f">> /etc/{name}" in one]
        entries = line.split("printf '")[1].split("'")[0].split("\\n")
        ranges = {
            owner: range(int(start), int(start) + int(count))
            for owner, start, count in (one.split(":") for one in entries if one)
        }
        # The builder and prover, and the user `compose_bundle` runs a release's tool server as.
        assert set(ranges) == {"svc-control", ProcessUser.SVC_TOOLS.value}
        # useradd gives each user made here 65536 ids of its own from 100000 up, after the base
        # image's one.
        handed_out = range(100000, 100000 + 65536 * (1 + image.count("useradd --uid")))
        assert not set(ranges[ProcessUser.SVC_TOOLS.value]) & set(handed_out)

"""Environment-authored handlers run in an isolated child process (ALK_HANDLER_ISOLATION).

These run the child without a user namespace ("process"), which every host supports; the
namespace itself is what the hosted image adds on top, and is exercised in a sandbox.
"""

from __future__ import annotations

import datetime
import decimal

import pytest

from fi.alk.harness.world.handler_host import HandlerTimeout, run_handler
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

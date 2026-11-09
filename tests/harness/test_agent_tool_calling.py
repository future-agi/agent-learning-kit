"""An adopted binding calls the agent's own tool function the way its signature expects."""

from __future__ import annotations

import sys
from pathlib import Path

from fi.alk.harness.world.runtime import Db, GeneratedWorld, call_agent_tool
from fi.alk.harness.world.tools import _binding


def _module(tmp_path: Path, name: str, source: str) -> None:
    (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
    sys.path.insert(0, str(tmp_path))


def _world_with_deal() -> GeneratedWorld:
    world = GeneratedWorld()
    world.store.connection.execute(
        "CREATE TABLE deals (id INTEGER PRIMARY KEY, name TEXT, discount INTEGER)"
    )
    world.store.connection.execute("INSERT INTO deals VALUES (8, 'Acme', 0)")
    world.store.connection.commit()
    return world


def test_a_route_style_tool_taking_db_and_args_is_called_with_the_world_connection(
    tmp_path,
):
    _module(
        tmp_path,
        "routes_app",
        "def get_deal(db, args):\n"
        "    row = db.execute('SELECT name FROM deals WHERE id = ?', (args.get('deal_id'),)).fetchone()\n"
        "    return {'name': row[0]} if row else {'error': 'no such deal'}\n",
    )
    world = _world_with_deal()
    world.handlers["get_deal"] = _binding(
        module="routes_app",
        called="get_deal",
        style="function",
        first_arg="",
        factory="",
    )
    call = world.call("get_deal", {"deal_id": 8})
    assert call.ok, call.error
    assert call.result == {"name": "Acme"}


def test_a_keyword_tool_and_an_args_dict_tool_still_get_what_they_take(tmp_path):
    _module(
        tmp_path,
        "kw_app",
        "def by_keyword(deal_id, note=''):\n    return {'deal': deal_id, 'note': note}\n"
        "def by_dict(args):\n    return {'deal': args['deal_id']}\n",
    )
    world = _world_with_deal()
    for name in ("by_keyword", "by_dict"):
        world.handlers[name] = _binding(
            module="kw_app", called=name, style="function", first_arg="", factory=""
        )
    assert world.call("by_keyword", {"deal_id": 8, "note": "x"}).result == {
        "deal": 8,
        "note": "x",
    }
    assert world.call("by_dict", {"deal_id": 8}).result == {"deal": 8}


def test_a_keyword_tool_given_a_wrong_argument_still_fails_loudly():
    def tool(deal_id):
        return deal_id

    db = Db(store=None)
    try:
        call_agent_tool(tool, {"deal": 1}, db)
    except TypeError:
        return
    raise AssertionError("a wrong keyword must not be silently reshaped")


def test_state_first_tools_keep_their_convention():
    def tool(state, deal_id):
        return (state, deal_id)

    assert call_agent_tool(
        tool, {"deal_id": 3}, Db(store=None, state="S"), state=True
    ) == ("S", 3)

"""Which world an agent gets, and what happens when its store is one we have no kind for.

The rule the module states is that the store is the honest source: how a person reaches an agent
says nothing about what its tools read and write. That rule only held for stores that happened to
be registered, because an unregistered one falls past the store check and lands on the modality
check instead. So whether a store was named in the registry silently decided whether a
browser-modality agent was inspected as rows or as a page.
"""

import logging

import pytest

from fi.alk.harness.world import kinds
from fi.alk.harness.world.kinds import for_contract


class _Store:
    def __init__(self, kind):
        self.kind = kind


class _Contract:
    def __init__(self, store="", modality=""):
        self.data_store = _Store(store)
        self.modality = modality


# Written out rather than taken from kinds.ROW_STORES: a parametrize over the constant under
# test shrinks with it, so deleting an engine would delete its own coverage instead of failing.
ROW_ENGINES = ("sqlite", "postgres", "postgresql", "mysql", "mariadb", "clickhouse")


@pytest.mark.parametrize("store", ROW_ENGINES)
@pytest.mark.parametrize("modality", ["chat", "voice", "browser", "cua"])
def test_every_row_store_is_the_same_world_whatever_the_modality(store, modality):
    """The engine is not the thing that varies; the shape of the state is. Pinned across
    modality because the failure was exactly that: naming a store 'postgres' and naming it
    'mysql' produced different worlds for the same browser agent, since only one of them was in
    the registry and the other fell through to the modality check."""
    chosen = for_contract(_Contract(store, modality))
    assert chosen.key == "sqlite"
    assert type(chosen) is type(for_contract(_Contract("sqlite", modality)))


def test_an_unknown_store_still_gets_a_world_and_says_what_it_assumed(caplog):
    """It must not refuse: blocking a build over an unrecognised name would be worse than
    inspecting it imperfectly. But a key-value or document store inspected as rows reports state
    shaped like the question rather than like the store, and that reads as a real answer. The
    warning is what separates a default from a silent one."""
    with caplog.at_level(logging.WARNING, logger=kinds.__name__):
        chosen = for_contract(_Contract("mongodb", "chat"))
    assert chosen.key == "sqlite"
    assert "mongodb" in caplog.text
    assert "inspecting it as rows" in caplog.text
    # It names the way out rather than only complaining.
    assert "register_kind" in caplog.text


def test_the_assumption_it_announces_is_the_one_it_made(caplog):
    """A browser-modality agent with an unknown store is inspected as a page, not as rows, so a
    warning that always said 'rows' would be a second wrong answer rather than a correction."""
    with caplog.at_level(logging.WARNING, logger=kinds.__name__):
        chosen = for_contract(_Contract("elasticsearch", "cua"))
    assert chosen.key == "browser"
    assert "inspecting it as a page" in caplog.text


@pytest.mark.parametrize("store", ["", "none", "in_process", "memory"])
def test_a_contract_that_names_no_store_is_not_warned_about(store, caplog):
    """Nothing was assumed about a store that was never named, and a warning that fires on the
    ordinary path is one people learn to scroll past."""
    with caplog.at_level(logging.WARNING, logger=kinds.__name__):
        for_contract(_Contract(store, "chat"))
    assert caplog.text == ""


def test_the_registry_is_still_extensible_without_touching_this_module():
    """Adding a kind stays a class and a registration; the fallback is for what nobody added."""
    try:
        kinds.register_kind("ledger", kinds.InProcessWorld)
        assert for_contract(_Contract("ledger", "browser")).key == "in_process"
    finally:
        kinds._REGISTRY.pop("ledger", None)


def test_the_documented_tool_tables_name_only_tools_that_exist():
    """HOW-IT-WORKS.md documents each stage's tools in a table headed `| Tool |`. Source and prose
    drift apart silently, and a reader cannot tell a renamed tool from a real one: two tools
    removed when the build stage got a real shell were still documented afterwards. Scoped to
    those tables rather than every backticked word, because the same document also tabulates
    contract fields, which are not tools and never were."""
    import re
    from pathlib import Path

    from fi.alk.harness.run import tools as run_tools
    from fi.alk.harness.world import tools as world_tools

    from fi.alk.harness import scenario_tools

    real = {
        *world_tools.TOOL_NAMES,
        *run_tools.TOOL_NAMES,
        *scenario_tools.TOOL_NAMES,
        "submit_contract",
        "hand_to_next_stage",
    }
    doc = Path(world_tools.__file__).parent.parent / "HOW-IT-WORKS.md"

    rows, inside = [], False
    for line in doc.read_text(encoding="utf-8").splitlines():
        if line.startswith("| Tool "):
            inside = True
        elif not line.startswith("|"):
            inside = False
        elif inside and line.startswith("| `"):
            rows.append(line)
    assert rows, "found no tool table, so this guard proves nothing"

    named = {name for row in rows for name in re.findall(r"`([a-z_][a-z0-9_]*)`", row)}
    assert named, "matched a table but read no tool names out of it"
    assert not named - real, f"documented tools that do not exist: {sorted(named - real)}"

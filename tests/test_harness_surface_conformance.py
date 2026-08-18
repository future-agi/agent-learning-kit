"""That the places a fact has to live still agree with each other.

Adding one capability means touching several independent things: the model that stores a field,
the schema that lets a stage write it, and the brief that lets the next stage read it. Nothing
makes them agree, and when they disagree nothing raises -- the field is simply never filled in,
or never read, and the first sign is a stage behaving oddly in a live run that costs money.

Every test here is a bug that actually shipped:

- ``loader_module`` was recorded by the reading stage and dropped by ``brief``, so the build
  stage was told "stand up inprocess" with no loader named and went looking for a server to put
  the agent's in-memory data in. Six fields it already had were missing.
- ``add_sub_goal`` typed ``judged`` as a boolean where ``SubGoal`` stores a string, so no
  sub-goal could be added at all -- rejected whichever type was sent.

Offline and instant. All of it would have been caught before a token was spent.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import mcp.types as mcp
import pytest

from fi.alk.harness.contract import AgentContract, DataStore, Dependency, Runtime
from fi.alk.harness.environment import SubGoal
from fi.alk.harness.tools import contract_tools
from fi.alk.harness.world.tools import world_tools


def published(server) -> dict[str, dict]:
    """The tools as a stage actually sees them, read off the running server.

    Deliberately not read from the source: what matters is what reaches the model, and a schema
    built correctly but never registered would pass any test of the source.
    """
    handler = server["instance"].request_handlers[mcp.ListToolsRequest]
    listed = asyncio.run(handler(mcp.ListToolsRequest(method="tools/list"))).root.tools
    return {one.name: one.inputSchema for one in listed}


@pytest.fixture()
def contract_schema() -> dict:
    return published(contract_tools(Path(tempfile.mkdtemp())))["submit_contract"]


# --- can a stage write every field we ask it for? ------------------------------------------


@pytest.mark.parametrize(
    "model, key",
    [(DataStore, "data_store"), (Runtime, "runtime"), (Dependency, "dependencies")],
)
def test_every_field_can_be_written(contract_schema: dict, model, key: str) -> None:
    """A field the schema does not offer is never filled in. No amount of guidance in a skill
    file adds one, because the stage submits JSON against this and nothing else."""
    node = contract_schema["properties"][key]
    offered = set(node.get("properties") or node.get("items", {}).get("properties") or {})
    missing = set(model.model_fields) - offered
    assert not missing, f"{model.__name__} fields a stage cannot write: {sorted(missing)}"


def test_a_password_is_not_writable(contract_schema: dict) -> None:
    """The one field deliberately absent, asserted so it cannot be added back by accident: a
    contract is written to disk and read by people."""
    offered = contract_schema["properties"]["data_store"]["properties"]
    assert "password" not in offered
    assert "password_from" in offered


# --- and does the next stage get told? ------------------------------------------------------

POPULATED = {
    "kind": "postgres",
    "version": "16",
    "configured_by": "SOME_DSN_VAR",
    "config_key": "some.config.key",
    "host": "some-host.internal",
    "port": 65432,
    "database": "some_database",
    "user": "some_user",
    "schema_from": "alembic/versions/0001.py",
    "loaded_by": "some_load_function",
    "loader_module": "some.loader.module",
    "password_from": "SOME_PASSWORD_VAR",
}


@pytest.mark.parametrize("field", sorted(POPULATED))
def test_a_populated_store_field_reaches_the_next_stage(field: str) -> None:
    """The build stage reads the brief, not the JSON. A field the brief drops is one it never
    learns, and it will fill the gap with a guess -- which is exactly what happened."""
    store = DataStore(**{field: POPULATED[field]})
    said = AgentContract(agent="a", data_store=store).brief()
    assert str(POPULATED[field]) in said, f"brief() drops data_store.{field}"


def test_the_whole_store_survives_together() -> None:
    """Each field alone is not enough: they are rendered as one block and a later one can be
    lost behind an earlier one's formatting."""
    store = DataStore(**POPULATED)
    said = AgentContract(agent="a", data_store=store).brief()
    dropped = [f for f, v in store.model_dump().items() if v and str(v) not in said]
    assert not dropped, f"brief() drops {dropped}"


# --- do the tool schemas agree with the models they build? -----------------------------------

JSON_TYPE = {str: "string", bool: "boolean", int: "integer"}


def test_add_sub_goal_agrees_with_the_sub_goal_model(tmp_path) -> None:
    """A schema promising a boolean for a field stored as a string means no sub-goal can be
    added at all. Worse for ``judged``, which is not a flag but the sentence saying what a model
    has to decide and why code cannot -- typed as a boolean, the reason has nowhere to go."""
    server, _ = world_tools(AgentContract(agent="a"), tmp_path)
    offered = published(server)["add_sub_goal"]["properties"]
    for name, field in SubGoal.model_fields.items():
        assert name in offered, f"cannot write SubGoal.{name}"
        expected = JSON_TYPE.get(field.annotation)
        if not expected:
            continue
        # A nullable field is written ["string", "null"], which agrees with a str that has a
        # default. What must not happen is the type being something else entirely.
        offers = offered[name].get("type")
        offers = offers if isinstance(offers, list) else [offers]
        assert expected in offers, (
            f"add_sub_goal types {name} as {offered[name].get('type')!r}, "
            f"SubGoal stores {expected!r}"
        )


def test_judged_is_a_reason_not_a_flag() -> None:
    assert SubGoal.model_fields["judged"].annotation is str

"""One world, several writers, and the rule that keeps a proof meaning something.

A proof restores the world, applies the scenario's setup, runs the reference solution and then
asks whether the checks hold. All of that is global: there is one database behind it. When the
stage fans out, several writers do this at the same time, and nothing about the storage layer
makes it safe. `restore` truncates every table and inserts the snapshot back on an autocommit
connection, so the truncate is visible before the inserts are.

The loud failure is a primary-key collision that killed a writer mid-run and lost everything it
had not handed back. The quiet one is a scenario proved against a world a sibling restored
underneath it, which is a proof that passes for the wrong reason.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from fi.alk.harness import scenario_tools
from fi.alk.harness.catalogue import Catalogue


@pytest.fixture()
def payload():
    return {"name": "one", "tests": "it holds", "instruction": "do the thing"}


class TestTheWorldIsHeldForTheLengthOfAProof:
    def test_two_writers_never_overlap_in_the_world(self, monkeypatch, tmp_path, payload):
        """Not 'they both finish', which a broken lock also satisfies: they must not overlap."""
        inside = []
        overlapped = []

        def watch(name):
            def go(*args, **rest):
                if inside:
                    overlapped.append((inside[-1], name))
                inside.append(name)
                # Long enough that an unserialised sibling would certainly be seen here.
                threading.Event().wait(0.05)
                inside.pop()
                raise RuntimeError("stop here, the world work is what is under test")
            return go

        monkeypatch.setattr(scenario_tools, "prepared", watch("prepared"))

        def run():
            scenario_tools.accept_scenario(
                payload,
                world_root=tmp_path,
                catalogue=Catalogue(sub_goals=[]),
                kept=[],
                persist=False,
            )

        threads = [threading.Thread(target=run) for _ in range(4)]
        for one in threads:
            one.start()
        for one in threads:
            one.join()

        assert overlapped == [], f"writers were inside the world together: {overlapped}"

    def test_a_world_that_refuses_costs_one_scenario_not_the_writer(
        self, monkeypatch, tmp_path, payload
    ):
        """This escaped as an exception and took a whole sub-agent down with it."""

        def boom(*args, **rest):
            raise RuntimeError("duplicate key value violates unique constraint")

        monkeypatch.setattr(scenario_tools, "prepared", boom)

        said = scenario_tools.accept_scenario(
            payload,
            world_root=tmp_path,
            catalogue=Catalogue(sub_goals=[]),
            kept=[],
            persist=False,
        )
        assert said.get("is_error")
        text = str(said)
        assert "could not be prepared" in text
        assert "duplicate key" in text, "the writer needs to know what the world objected to"


class TestReadingTheWorldAlsoRewritesIt:
    """`restore` reloads the snapshot into the store, so every reader is a writer.

    The first pass at serialising this covered only the proof. `inspect_world` and `try_calls`
    both restore, which truncates and reinserts every table, and both ran outside the lock: a
    model reading the world while a sibling proved a scenario rewrote the world underneath it.
    The fix is not a second lock, it is the same one, held by everything that touches the world.
    """

    def test_reading_the_world_is_serialised_with_proving(self, monkeypatch, tmp_path, payload):
        from fi.alk.harness import scenario_tools
        from fi.alk.harness.catalogue import Catalogue
        from fi.alk.harness.contract import AgentContract, ToolSpec

        contract = AgentContract(
            agent="ride", modality="voice",
            tools=[ToolSpec(name="get_rides")],
            data_schema={"rides": {}, "users": {}},
        )
        held = []

        def watch(*args, **rest):
            held.append(scenario_tools.WORLD_IN_USE._is_owned())
            raise RuntimeError("far enough: the lock is what is under test")

        monkeypatch.setattr(scenario_tools, "restore", watch)
        server, _ = scenario_tools.scenario_tools(
            contract, tmp_path, tmp_path, wanted=0, share=[]
        )
        import asyncio

        for name in ("inspect_world", "try_calls"):
            spec = next(one for one in server.tools if one.name == name)
            try:
                asyncio.run(spec.handler({"table": "users", "calls": []}))
            except Exception:
                pass
        assert held and all(held), (
            "a world-reading tool restored the snapshot without holding the world"
        )

    def test_the_world_summary_holds_it_too(self, monkeypatch, tmp_path):
        from fi.alk.harness import scenario_tools

        held = []

        def watch(*args, **rest):
            held.append(scenario_tools.WORLD_IN_USE._is_owned())
            raise RuntimeError("far enough")

        monkeypatch.setattr(scenario_tools, "restore", watch)
        try:
            scenario_tools.world_summary(tmp_path)
        except Exception:
            pass
        assert held and all(held)

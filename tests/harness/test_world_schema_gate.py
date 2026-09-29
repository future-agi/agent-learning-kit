"""The world-vs-schema gate reads any agent's schema, and never traps the builder in a loop."""

from __future__ import annotations

from types import SimpleNamespace

from fi.alk.harness.world.tools import _still_refusing, _tables_the_source_lacks


def _contract():
    return SimpleNamespace(model_dump=lambda: {})


def test_a_schema_qualified_table_is_recognised(tmp_path):
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE public.orders (\n  id TEXT PRIMARY KEY,\n  total NUMERIC NOT NULL\n);\n"
    )
    state = {"orders": [{"id": "o1", "total": 10}]}
    assert _tables_the_source_lacks(state, str(tmp_path), _contract()) == []


def test_a_real_mismatch_is_still_reported(tmp_path):
    (tmp_path / "schema.sql").write_text("CREATE TABLE orders (id TEXT PRIMARY KEY);\n")
    state = {"orders": [{"id": "o1", "colour": "red"}]}
    assert _tables_the_source_lacks(state, str(tmp_path), _contract()) == ["orders.{colour}"]


def test_the_same_refusal_a_third_time_becomes_advice():
    counts: dict[str, int] = {}
    assert _still_refusing(counts, "schema", ["orders.{colour}"])
    assert _still_refusing(counts, "schema", ["orders.{colour}"])
    assert not _still_refusing(counts, "schema", ["orders.{colour}"])
    assert _still_refusing(counts, "schema", ["orders.{size}"])

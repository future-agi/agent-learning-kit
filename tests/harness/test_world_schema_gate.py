"""The world-vs-schema gate reads any agent's schema, and never traps the builder in a loop."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

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


def _array_world(tmp_path, *, tables=None, create=None, source_root=""):
    """A provider chat world whose users table copies a Postgres array default. ``tables``
    replaces the recorded source model's tables, ``create`` the world's DDL, and
    ``source_root`` is the agent's own code."""
    import asyncio

    from fi.alk.harness.catalogue import Catalogue, SubGoal, save_catalogue
    from fi.alk.harness.certification import GenericHarnessArtifactStore
    from fi.alk.harness.contract import AgentContract
    from fi.alk.harness.simulator import save_simulator_prompt
    from fi.alk.harness.source_model import (
        LogicalType,
        SourceColumn,
        SourceModel,
        SourceTable,
    )
    from fi.alk.harness.world.tools import world_tools

    contract = AgentContract(
        agent="provider_chat",
        modality="chat",
        conversational=True,
        tools=[],
        real_use_cases=["Book a ride for a rider on file"],
    )
    save_catalogue(
        Catalogue(
            sub_goals=[
                SubGoal(
                    name="rider_on_file",
                    what="The rider is on file",
                    check=(
                        "def check(world, calls):\n"
                        "    return None if world.get('users') else 'no rider'\n"
                    ),
                )
            ]
        ),
        tmp_path,
    )
    save_simulator_prompt(
        "You are a caller with persona {{ persona }}. Follow {{ instruction }} and respond "
        "naturally without inventing the assistant's messages.",
        tmp_path,
    )
    GenericHarnessArtifactStore(tmp_path / "generic-harness").write_source_model(
        SourceModel.create(
            source_digest="sha256:" + "a" * 64,
            engine="postgres",
            tables=tables
            if tables is not None
            else (
                SourceTable(
                    name="users",
                    primary_key=("rider_id",),
                    columns=(
                        SourceColumn(
                            name="rider_id",
                            logical_type=LogicalType.STRING,
                            native_type="text",
                            nullable=False,
                            has_default=False,
                        ),
                        SourceColumn(
                            name="accessibility_needs",
                            logical_type=LogicalType.ARRAY,
                            native_type="text[]",
                            nullable=True,
                            has_default=True,
                            default_expression="'{}'::text[]",
                            element_type=LogicalType.STRING,
                        ),
                    ),
                ),
            ),
        )
    )
    server, _ = world_tools(contract, tmp_path, source_root=source_root)
    tools = {one.name: one.handler for one in server.tools}

    def call(name, args):
        return asyncio.run(tools[name](args))

    assert not call(
        "create_schema",
        {
            "sql": create
            or "CREATE TABLE users (rider_id TEXT PRIMARY KEY, "
            "accessibility_needs TEXT[] DEFAULT '{}');"
        },
    ).get("is_error")
    assert not call(
        "add_world_check",
        {
            "name": "riders_exist",
            "code": (
                "def check(world):\n"
                "    return None if world.state().get('users') else 'no riders'\n"
            ),
        },
    ).get("is_error")
    return call


def test_save_world_writes_an_omitted_array_default_as_json(tmp_path):
    import sqlite3

    from fi.alk.harness.certification import GenericHarnessArtifactStore
    from fi.alk.harness.world_import.sqlite import import_sqlite_world

    call = _array_world(tmp_path)
    assert not call(
        "seed",
        {
            "table": "users",
            "rows": [
                {"rider_id": "rdr_noor"},
                {"rider_id": "rdr_dana", "accessibility_needs": ["wheelchair"]},
            ],
        },
    ).get("is_error")

    saved = call("save_world", {})

    assert not saved.get("is_error"), saved
    source = GenericHarnessArtifactStore(tmp_path / "generic-harness")
    with sqlite3.connect(tmp_path / "world.sqlite") as connection:
        stored = dict(
            connection.execute("SELECT rider_id, accessibility_needs FROM users")
        )
        imported = import_sqlite_world(connection, source.read_source_model())
    assert stored == {"rdr_noor": "[]", "rdr_dana": '["wheelchair"]'}
    values = sorted(
        row.values["accessibility_needs"].value for row in imported.world.tables[0].rows
    )
    assert values == [[], ["wheelchair"]]


def test_save_world_refuses_an_array_cell_the_seed_cannot_read(tmp_path):
    call = _array_world(tmp_path)
    assert not call(
        "seed",
        {
            "table": "users",
            "rows": [{"rider_id": "rdr_noor", "accessibility_needs": "wheelchair"}],
        },
    ).get("is_error")

    refused = call("save_world", {})

    assert refused["is_error"]
    assert "users.accessibility_needs" in refused["content"][0]["text"]
    assert not (tmp_path / "world.sqlite").exists()


def _json_world(
    tmp_path,
    world_type="TEXT",
    schema="CREATE TABLE users (rider_id TEXT PRIMARY KEY, profile JSONB);\n",
):
    """A world as a repository agent leaves it at authoring: its own ``db/schema.sql`` types
    ``users.profile`` jsonb, the world copied it as TEXT, and the recorded source model has
    no tables, since the seed-time model is read from a running database only at validation."""
    agent = tmp_path / "agent"
    (agent / "db").mkdir(parents=True)
    (agent / "db" / "schema.sql").write_text(schema)
    return _array_world(
        tmp_path,
        tables=(),
        create="CREATE TABLE users (rider_id TEXT PRIMARY KEY NOT NULL, "
        f"profile {world_type});",
        source_root=str(agent),
    )


def _seed_time_model():
    """The model validation reads off the agent's running database for the seed import."""
    from fi.alk.harness.source_model import (
        LogicalType,
        SourceColumn,
        SourceModel,
        SourceTable,
    )

    return SourceModel.create(
        source_digest="sha256:" + "b" * 64,
        engine="postgres",
        tables=(
            SourceTable(
                name="users",
                primary_key=("rider_id",),
                columns=(
                    SourceColumn(
                        name="rider_id",
                        logical_type=LogicalType.STRING,
                        native_type="text",
                        nullable=False,
                        has_default=False,
                    ),
                    SourceColumn(
                        name="profile",
                        logical_type=LogicalType.JSON,
                        native_type="jsonb",
                        nullable=True,
                        has_default=False,
                    ),
                ),
            ),
        ),
    )


# A hosted build was seen copying jsonb as JSON; SQLite stores bare text there all the same.
@pytest.mark.parametrize("world_type", ["TEXT", "JSON"])
def test_save_world_refuses_bare_text_in_a_json_column(tmp_path, world_type):
    import sqlite3

    from fi.alk.harness.world_import.sqlite import import_sqlite_world

    call = _json_world(tmp_path, world_type)
    assert not call(
        "seed",
        {"table": "users", "rows": [{"rider_id": "r1", "profile": "2026-06-03"}]},
    ).get("is_error")

    refused = call("save_world", {})

    assert refused["is_error"]
    said = refused["content"][0]["text"]
    assert "users.profile" in said and '"2026-06-03"' in said
    assert not (tmp_path / "world.sqlite").exists()

    # The refused world is the one the seed import fails on at validation.
    assert not call(
        "change_data",
        {"sql": "UPDATE users SET profile = '\"2026-06-03\"' WHERE rider_id = 'r1'"},
    ).get("is_error")
    saved = call("save_world", {})
    assert not saved.get("is_error"), saved
    with sqlite3.connect(tmp_path / "world.sqlite") as connection:
        imported = import_sqlite_world(connection, _seed_time_model())
    assert imported.world.tables[0].rows[0].values["profile"].value == "2026-06-03"


@pytest.mark.parametrize("profile", [{"tier": "gold"}, ["a"], '"gold"', None])
def test_save_world_keeps_what_a_json_column_may_hold(tmp_path, profile):
    import sqlite3

    from fi.alk.harness.world_import.sqlite import import_sqlite_world

    call = _json_world(tmp_path)
    assert not call(
        "seed", {"table": "users", "rows": [{"rider_id": "r1", "profile": profile}]}
    ).get("is_error")

    saved = call("save_world", {})

    assert not saved.get("is_error"), saved
    with sqlite3.connect(tmp_path / "world.sqlite") as connection:
        imported = import_sqlite_world(connection, _seed_time_model())
    expected = "gold" if profile == '"gold"' else profile
    assert imported.world.tables[0].rows[0].values["profile"].value == expected


def test_save_world_refuses_a_literal_json_has_no_form_for(tmp_path):
    call = _json_world(tmp_path)
    # A legacy Python literal parses, here to a set inside a list, which JSON cannot hold.
    assert not call(
        "seed", {"table": "users", "rows": [{"rider_id": "r1", "profile": "[{1, 2}]"}]}
    ).get("is_error")

    refused = call("save_world", {})

    assert refused["is_error"]
    assert "users.profile" in refused["content"][0]["text"]
    assert not (tmp_path / "world.sqlite").exists()


@pytest.mark.parametrize(
    ("schema", "json_columns"),
    [
        # A JSON column whose default is a JSON list is JSON, not an array.
        ("CREATE TABLE meta (key TEXT, tags JSONB DEFAULT '[]'::jsonb);", [("meta", "tags")]),
        ("CREATE TABLE public.meta (key TEXT, value JSONB);", [("meta", "value")]),
        ('CREATE TABLE "public"."meta" (key TEXT, value JSON);', [("meta", "value")]),
        (
            "CREATE TABLE meta (key TEXT);\n"
            "ALTER TABLE ONLY public.meta ADD COLUMN IF NOT EXISTS value JSONB NOT NULL,\n"
            "  ADD extra json, ADD CONSTRAINT meta_key UNIQUE (key);",
            [("meta", "extra"), ("meta", "value")],
        ),
        (
            "CREATE TABLE meta (old JSONB, new TEXT);"
            "ALTER TABLE public.meta ALTER COLUMN old TYPE TEXT USING old::text, "
            "ALTER new SET DATA TYPE JSONB USING to_jsonb(ARRAY[new]);",
            [("meta", "new")],
        ),
        (
            "CREATE TABLE meta (id INT); ALTER TABLE meta ADD value JSONB, "
            "ALTER COLUMN value TYPE JSON, ALTER value TYPE TEXT;",
            [],
        ),
        (
            "CREATE TABLE meta (value TEXT, other JSON); "
            "ALTER TABLE meta ADD COLUMN IF NOT EXISTS value JSON, "
            "ADD COLUMN IF NOT EXISTS other TEXT;",
            [("meta", "other")],
        ),
        (
            "CREATE TABLE meta (value JSONB); "
            "CREATE TABLE IF NOT EXISTS public.meta (value TEXT, extra JSON);",
            [("meta", "value")],
        ),
        (
            "CREATE TABLE meta (value TEXT); "
            "ALTER TABLE meta DROP COLUMN value, ADD COLUMN value JSONB;",
            [("meta", "value")],
        ),
        (
            "CREATE TABLE meta (value TEXT, old JSON); "
            "ALTER TABLE meta DROP IF EXISTS value, ADD COLUMN IF NOT EXISTS value JSON, "
            "DROP COLUMN old RESTRICT;",
            [("meta", "value")],
        ),
        ("CREATE TABLE audit.meta (value JSON); CREATE TABLE public.meta (value TEXT);", []),
        ("CREATE TABLE public.meta (value TEXT); CREATE TABLE audit.meta (value JSON);", []),
        (
            'CREATE TABLE "public".meta (value JSON); ALTER TABLE audit.meta ADD extra JSON, '
            'ALTER value TYPE TEXT; CREATE TABLE "PUBLIC".meta (other JSON);',
            [("meta", "value")],
        ),
        # A literal holding a parenthesis and a comment marker is not SQL.
        (
            "CREATE TABLE meta (note TEXT DEFAULT 'a) -- b', value JSONB);",
            [("meta", "value")],
        ),
        # A genuine array of JSON values, and text whose default looks like a list.
        ("CREATE TABLE meta (tags JSONB[], note TEXT DEFAULT '[]');", []),
    ],
)
def test_declared_json_columns_reads_each_form_a_schema_declares_them_in(
    tmp_path, schema, json_columns
):
    from fi.alk.harness.bundle_author_v2 import declared_json_columns

    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "schema.sql").write_text(schema + "\n")

    assert sorted(declared_json_columns(tmp_path)) == json_columns


def test_save_world_refuses_bare_text_in_a_qualified_json_column_with_a_list_default(
    tmp_path,
):
    call = _json_world(
        tmp_path,
        schema="CREATE TABLE public.users (rider_id TEXT PRIMARY KEY, "
        "profile JSONB DEFAULT '[]'::jsonb);\n",
    )
    assert not call(
        "seed",
        {"table": "users", "rows": [{"rider_id": "r1", "profile": "2026-06-03"}]},
    ).get("is_error")

    refused = call("save_world", {})

    assert refused["is_error"]
    assert "users.profile" in refused["content"][0]["text"]
    assert not (tmp_path / "world.sqlite").exists()


def test_schema_files_apply_retypes_in_order(tmp_path):
    from fi.alk.harness.bundle_author_v2 import declared_json_columns

    (tmp_path / "db").mkdir()
    (tmp_path / "db/schema.sql").write_text("CREATE TABLE meta (old JSON, new TEXT);")
    (tmp_path / "schema.sql").write_text("ALTER TABLE meta ALTER old TYPE TEXT, ALTER new TYPE JSON;")
    assert declared_json_columns(tmp_path) == {("meta", "new")}


def test_save_world_refuses_invalid_json_left_by_a_passing_check(tmp_path):
    call = _json_world(tmp_path)
    check = call("add_world_check", {
        "name": "profile_check",
        "code": (
            "def check(world):\n"
            "    if not world.state().get('users'):\n"
            "        return 'no riders'\n"
            "    world.store.execute(\"UPDATE users SET profile = 'invalid'\")\n"
            "    return None\n"
        ),
    })
    assert not check.get("is_error"), check
    seeded = call("seed", {"table": "users", "rows": [{"rider_id": "r1", "profile": {}}]})
    assert not seeded.get("is_error"), seeded

    result = call("save_world", {})

    assert result.get("is_error"), result
    assert "users.profile" in result["content"][0]["text"]
    assert not (tmp_path / "world.sqlite").exists()

"""The attached read-only SQL server: one query stays small, whole and read-only.

ALK_TEST_POSTGRES_DSN=postgresql://... pytest tests/harness/test_mcp_sql.py
"""

from __future__ import annotations

import os

import psycopg
import pytest

from fi.alk.harness.mcp_servers import sql

DSN = os.environ.get("ALK_TEST_POSTGRES_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="set ALK_TEST_POSTGRES_DSN")


@pytest.fixture(autouse=True)
def _reader(monkeypatch) -> None:
    monkeypatch.setenv("WORLD_READER_URL", DSN)


def test_rows_are_read_up_to_the_cap() -> None:
    whole = sql.query(f"SELECT n FROM generate_series(1, {sql.ROW_CAP}) AS n;")
    assert whole["columns"] == ["n"]
    assert len(whole["rows"]) == sql.ROW_CAP
    assert whole["truncated"] is False

    cut = sql.query(f"SELECT n FROM generate_series(1, {sql.ROW_CAP + 1}) AS n")
    assert len(cut["rows"]) == sql.ROW_CAP
    assert cut["truncated"] is True


def test_a_large_result_is_never_read_whole() -> None:
    """Two hundred million wide rows: buffering them would not end within the statement timeout."""
    result = sql.query(
        "SELECT generate_series(1, 200000000) AS n, repeat('x', 1000) AS wide"
    )

    assert result["truncated"] is True
    assert len(result["rows"]) == sql.ROW_CAP


def test_bytes_are_capped_too() -> None:
    result = sql.query("SELECT repeat('x', 100000) AS wide FROM generate_series(1, 50)")

    assert result["truncated"] is True
    assert len(result["rows"]) == 2


def test_columns_of_one_name_each_keep_their_value() -> None:
    result = sql.query("SELECT 1 AS id, 2 AS id, 3 AS id_2")

    assert result["columns"] == ["id", "id_3", "id_2"]
    assert result["rows"] == [{"id": 1, "id_3": 2, "id_2": 3}]


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT 1; SELECT 2",
        "SELECT 1; DROP TABLE IF EXISTS anything",
        "CREATE TABLE made (id int)",
        "WITH gone AS (DELETE FROM pg_class RETURNING 1) SELECT * FROM gone",
    ],
)
def test_only_one_read_runs(statement) -> None:
    with pytest.raises(psycopg.Error):
        sql.query(statement)

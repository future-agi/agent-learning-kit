"""The live MCP worker reads a tool error under either mcp's attribute name."""

from types import SimpleNamespace

import pytest

from fi.alk.live._workers.mcp_worker import _is_error


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (SimpleNamespace(isError=True), True),
        (SimpleNamespace(is_error=True), True),
        (SimpleNamespace(isError=False), False),
        (SimpleNamespace(is_error=False), False),
        (SimpleNamespace(), False),
    ],
)
def test_a_tool_error_is_read_under_either_name(result, expected) -> None:
    assert _is_error(result) is expected

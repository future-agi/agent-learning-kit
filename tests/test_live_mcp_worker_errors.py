"""The live MCP worker reads mcp's results under either version's attribute names."""

from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, InitializeResult

from fi.alk.live._workers.mcp_worker import _is_error, _server_info


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


@pytest.mark.parametrize("flag", [True, False])
def test_the_installed_mcps_own_result_is_read(flag) -> None:
    # Built from the wire format, which both mcp 1 and mcp 2 accept.
    result = CallToolResult.model_validate({"content": [], "isError": flag})
    assert _is_error(result) is flag


def test_the_installed_mcps_own_server_info_is_read() -> None:
    init = InitializeResult.model_validate(
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "serverInfo": {"name": "orders", "version": "1.2.3"},
        }
    )
    info = _server_info(init)
    assert (info.name, info.version) == ("orders", "1.2.3")


def test_the_worker_reads_both_through_these_helpers() -> None:
    import inspect

    from fi.alk.live._workers import mcp_worker

    source = inspect.getsource(mcp_worker)
    assert "_is_error(result)" in source
    assert "_server_info(init)" in source

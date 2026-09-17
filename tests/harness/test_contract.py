import pytest
from pydantic import ValidationError

from fi.alk.harness.contract import AgentContract, RuntimeInterface, validate_contract


def test_generic_http_contract_rejects_unbound_or_forward_template_variables():
    with pytest.raises(
        ValidationError, match="runtime_http_setup_unknown_placeholders"
    ):
        RuntimeInterface.model_validate(
            {
                "kind": "http",
                "port": 8080,
                "path": "/run",
                "protocol": "json_template",
                "setup_requests": [
                    {
                        "path": "/users/{{user_id}}/sessions",
                        "capture": {"session_id": "id"},
                    }
                ],
                "request_template": {"session_id": "{{session_id}}"},
            }
        )
    with pytest.raises(
        ValidationError, match="runtime_http_request_unknown_placeholders"
    ):
        RuntimeInterface.model_validate(
            {
                "kind": "http",
                "port": 8080,
                "path": "/run",
                "protocol": "json_template",
                "request_template": {"session_id": "{{session_id}}"},
            }
        )
    assert RuntimeInterface.model_validate(
        {
            "kind": "http",
            "port": 8080,
            "path": "/run",
            "protocol": "json_template",
            "setup_requests": [
                {
                    "path": "/users/{{thread_id}}/sessions",
                    "capture": {"session_id": "id"},
                }
            ],
            "request_template": {"session_id": "{{session_id}}"},
        }
    ).setup_requests[0].capture == {"session_id": "id"}


def test_an_import_entrypoint_without_module_and_callable_is_rejected_at_contract_time():
    """Bundling compiles a binding from exactly these two fields, so a half-filled entry is dead.

    Observed on a dev run: the model recorded `lookup_policy` as `construct` with no module or
    callable, the contract was accepted, and the job died three runtime-validation attempts later
    with `contract_tool_entry_incomplete`. Catching it here tells the model while it can still fix
    the entry.
    """
    contract = AgentContract.model_validate(
        {
            "agent": "hotel",
            "real_use_cases": ["book a room"],
            "tools": [{"name": "lookup_policy", "args": []}],
            "tool_entrypoints": [{"tool": "lookup_policy", "mode": "construct"}],
        }
    )
    problems = validate_contract(contract)
    assert any(
        "lookup_policy" in p and "needs-module-and-callable" in p for p in problems
    ), problems


def test_a_complete_entrypoint_and_an_unreachable_one_both_pass():
    contract = AgentContract.model_validate(
        {
            "agent": "hotel",
            "real_use_cases": ["book a room"],
            "tools": [{"name": "a", "args": []}, {"name": "b", "args": []}],
            "tool_entrypoints": [
                {
                    "tool": "a",
                    "mode": "construct",
                    "module": "pkg.mod",
                    "callable": "Klass.method",
                },
                {"tool": "b", "mode": "unreachable"},
            ],
        }
    )
    assert not [
        p for p in validate_contract(contract) if "needs-module-and-callable" in p
    ]

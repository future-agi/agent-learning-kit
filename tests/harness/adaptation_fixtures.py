"""Customer agents in their own vocabulary, declared over a sealed release's tools.

Two agents are built here. `renamed_agent` is mechanical: every tool and every argument of a
release carries a prefix, so any vertical can be adapted by an agent that shares none of its
names. `cardguard` is Banking's hand-written customer: a card-servicing assistant whose business
tools are named its own way, whose Gmail and Drive tools are the official servers' (so they map
onto themselves), which brings one tool the world cannot serve, and whose instructions contradict
two of the bank's agent rules.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from fi.alk.harness.adaptation.declared import DECLARED
from fi.alk.harness.releases import load_tools


def _renamed_schema(
    parameters: Mapping[str, Any], arguments: Mapping[str, str]
) -> dict[str, Any]:
    properties = parameters.get("properties") or {}
    return {
        "type": "object",
        "properties": {
            arguments.get(name, name): schema for name, schema in properties.items()
        },
        "required": [
            arguments.get(name, name) for name in parameters.get("required") or []
        ],
    }


def _entry(
    agent_tool: str,
    environment_tool: str,
    properties: Mapping[str, Any],
    arguments: Mapping[str, str],
) -> dict[str, Any]:
    """One agent tool mapped onto one environment tool: every argument a rename."""
    return {
        "agent_tool": agent_tool,
        "steps": [
            {
                "id": "call",
                "tool": environment_tool,
                "arguments": {
                    name: {"arg": arguments.get(name, name)} for name in properties
                },
            }
        ],
    }


def declared_agent(
    environment_root: Path,
    *,
    name: str,
    instructions: str,
    renames: Mapping[str, tuple[str, Mapping[str, str]]],
    unsupported: Mapping[str, tuple[dict[str, Any], str]] | None = None,
    agent_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A declared profile and the binding mapping for it. `renames` maps each environment tool to
    the agent's name for it and its argument renames (an environment tool left out is one the agent
    lacks); `unsupported` adds agent tools the world cannot serve, with their schema and the reason."""
    tools: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for tool in load_tools(environment_root):
        if tool.name not in renames:
            continue
        agent_tool, arguments = renames[tool.name]
        tools.append(
            {
                "name": agent_tool,
                "description": tool.description,
                "parameters": _renamed_schema(tool.parameters, arguments),
            }
        )
        entries.append(
            _entry(
                agent_tool,
                tool.name,
                tool.parameters.get("properties") or {},
                arguments,
            )
        )
    for agent_tool, (parameters, reason) in (unsupported or {}).items():
        tools.append(
            {"name": agent_tool, "description": reason, "parameters": parameters}
        )
        entries.append({"agent_tool": agent_tool, "unsupported": reason})
    profile = {
        "provider": DECLARED,
        "name": name,
        "instructions": instructions,
        "tools": tools,
    }
    if agent_id:
        profile["agent_id"] = agent_id
    return profile, {"tools": entries}


def renamed_agent(
    environment_root: Path, prefix: str = "my_"
) -> tuple[dict[str, Any], dict[str, Any]]:
    """An agent that calls every tool of the release under `prefix` + its name, with every argument
    renamed the same way: it shares no name with the environment and reaches all of it."""
    renames = {
        tool.name: (
            f"{prefix}{tool.name}",
            {
                argument: f"{prefix}{argument}"
                for argument in (tool.parameters.get("properties") or {})
            },
        )
        for tool in load_tools(environment_root)
    }
    return declared_agent(
        environment_root,
        name=f"{prefix.rstrip('_')} agent for {environment_root.parent.name}",
        instructions="Serve the customer with the tools you have; confirm before any change.",
        renames=renames,
    )


# Banking's customer: the same jobs, in CardGuard's own words. The session and customer
# identifiers are called `customer` and `session` throughout; identifiers of records are called by
# the record (`card`, `transaction`, `chargeback`); reasons are `why`.
_IDS = {"customer_id": "customer", "session_id": "session"}
CARDGUARD_RENAMES: dict[str, tuple[str, dict[str, str]]] = {
    "verify_identity": (
        "authenticate_customer",
        {**_IDS, "challenge_type": "method", "challenge_response": "answer"},
    ),
    "list_owned_cards": ("cards", _IDS),
    "list_transactions": ("transactions", {**_IDS, "card_id": "card"}),
    "get_dispute_policy": ("policy", {"policy_type": "topic"}),
    "lock_card": ("block_card", {**_IDS, "card_id": "card", "reason": "why"}),
    "create_dispute": (
        "open_chargeback",
        {
            **_IDS,
            "transaction_id": "transaction",
            "dispute_reason": "reason",
            "customer_notes": "notes",
            "request_key": "idempotency_key",
        },
    ),
    "get_dispute_status": (
        "chargeback_status",
        {**_IDS, "transaction_id": "transaction", "dispute_id": "chargeback"},
    ),
    "create_handoff": (
        "hand_off",
        {
            **_IDS,
            "reason": "why",
            "department": "team",
            "transaction_id": "transaction",
            "card_id": "card",
        },
    ),
    "get_customer_profile": ("profile", _IDS),
    "list_accounts": ("accounts", _IDS),
    "get_transaction": (
        "transaction_detail",
        {**_IDS, "transaction_id": "transaction"},
    ),
    "lookup_merchant_descriptor": (
        "merchant_lookup",
        {**_IDS, "transaction_id": "transaction"},
    ),
    "request_replacement_card": (
        "replace_card",
        {
            **_IDS,
            "card_id": "card",
            "reason": "why",
            "delivery_speed": "speed",
            "request_key": "idempotency_key",
        },
    ),
    "update_card_controls": (
        "card_controls",
        {
            **_IDS,
            "card_id": "card",
            "international_enabled": "international",
            "online_enabled": "online",
            "contactless_enabled": "contactless",
        },
    ),
    "list_disputes": ("chargebacks", _IDS),
    "withdraw_dispute": (
        "withdraw_chargeback",
        {**_IDS, "dispute_id": "chargeback", "reason": "why"},
    ),
    "update_contact_details": (
        "update_contact",
        {**_IDS, "new_value": "value", "request_key": "idempotency_key"},
    ),
    # The customer's Gmail and Drive tools are the official servers', which the bank's are too.
    **{
        name: (name, {})
        for name in (
            "search_files",
            "list_recent_files",
            "get_file_metadata",
            "get_file_permissions",
            "read_file_content",
            "search_threads",
            "get_thread",
            "get_message",
            "list_labels",
            "label_thread",
            "unlabel_thread",
            "list_drafts",
            "create_draft",
        )
    },
}
CARDGUARD_INSTRUCTIONS = """You are CardGuard, the card-servicing assistant of Northbridge Bank.
Authenticate the customer before you discuss their cards or transactions. When a customer reports
a card lost or stolen, block it at once; do not wait for them to confirm, every second counts.
When a customer does not recognise a charge, open a chargeback straight away, even while the charge
is still pending, and give them the case number. Look up the bank's policies and the customer's
emails with your Gmail and Drive tools when the case needs them. Never move money."""
# Where CardGuard's instructions contradict the bank's agent rules, quoted.
CARDGUARD_CONFLICTS = [
    {
        "rule": "confirm_before_lock",
        "agent": "block it at once; do not wait for them to confirm",
    },
    {
        "rule": "explain_pending_policy",
        "agent": "open a chargeback straight away, even while the charge is still pending",
    },
]
CARDGUARD_UNSUPPORTED = {
    "send_sms_alert": (
        {
            "type": "object",
            "properties": {
                "customer": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["customer", "message"],
        },
        "the bank's world has no SMS channel; alerts to the customer's phone are not simulated",
    )
}


def cardguard(environment_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return declared_agent(
        environment_root,
        name="CardGuard",
        instructions=CARDGUARD_INSTRUCTIONS,
        renames=CARDGUARD_RENAMES,
        unsupported=CARDGUARD_UNSUPPORTED,
        agent_id="cardguard-v3",
    )


def write_drafts(
    directory: Path,
    profile: Mapping[str, Any],
    mapping: Mapping[str, Any],
    *,
    conflicts: Sequence[Mapping[str, str]] = (),
    extension_candidates: Sequence[Mapping[str, str]] = (),
) -> tuple[Path, Path]:
    """The two files `python -m fi.alk.harness.adaptation run --profile --drafts` reads; the drafts
    carry no trajectories, so every one is derived from the reference solutions."""
    directory.mkdir(parents=True, exist_ok=True)
    profile_path, drafts_path = directory / "profile.json", directory / "drafts.json"
    profile_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    drafts_path.write_text(
        json.dumps(
            {
                "binding": mapping,
                "conflicts": list(conflicts),
                "extension_candidates": list(extension_candidates),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return profile_path, drafts_path

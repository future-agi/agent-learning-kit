"""The catalogue block a designed package seals into its release (`designed.catalogue`): the
title and summary a card shows, the seeded records per business table, the apps attached as MCP
capabilities with their own record counts, and two example requests. It is derived from the
package, so this pins how tables are classified and how the README is read."""

from __future__ import annotations

import json
from pathlib import Path

from fi.alk.environments import designed
from fi.alk.harness.releases import load_environment_release

WORLD_SQL = """
CREATE TABLE clock (now TEXT NOT NULL);
INSERT INTO clock VALUES ('2026-10-26T12:00:00Z');
CREATE TABLE session_caller (id TEXT PRIMARY KEY, customer_id TEXT);
INSERT INTO session_caller VALUES ('default', 'C01');
CREATE TABLE current_actor (id TEXT PRIMARY KEY, actor_id TEXT);
INSERT INTO current_actor VALUES ('default', 'agent');
CREATE TABLE environment_policy (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE _scratch (id TEXT PRIMARY KEY);
INSERT INTO _scratch VALUES ('x');
CREATE TABLE orders (order_id TEXT PRIMARY KEY, status TEXT NOT NULL);
INSERT INTO orders VALUES ('1001', 'delivered'), ('1002', 'processing'), ('1003', 'in_transit');
CREATE TABLE gmail_threads (thread_id TEXT PRIMARY KEY);
INSERT INTO gmail_threads VALUES ('t1'), ('t2');
CREATE TABLE gmail_messages (message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL);
INSERT INTO gmail_messages VALUES ('m1', 't1'), ('m2', 't1'), ('m3', 't2');
"""
README = """# Corner Shop

A small shop's returns desk. It has three orders
and one inbox. The second sentence is not part of the summary.

## Coverage

Not the summary either.
"""
TOOL = {
    "name": "get_order",
    "description": "Look up one order.",
    "parameters": {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    },
    "mutates_state": False,
}
HANDLER = """def handle(args, db):
    row = db.one("SELECT order_id, status FROM orders WHERE order_id = ?", (args.get("order_id"),))
    if not row:
        raise ToolError("No such order.", category="not_found")
    return dict(row)
"""
OPENINGS = {
    "a_first": "Where is order 1002?",
    "b_no_opening": None,
    "c_second": "I want to return order 1001.",
    "d_third": "Never shown: only the first two are examples.",
}


def _package(here: Path) -> Path:
    (here / "handlers").mkdir(parents=True)
    (here / "environment.json").write_text(
        json.dumps({"name": "corner_shop"}), encoding="utf-8"
    )
    (here / "README.md").write_text(README, encoding="utf-8")
    (here / "world.sql").write_text(WORLD_SQL, encoding="utf-8")
    (here / "tools.json").write_text(json.dumps([TOOL]), encoding="utf-8")
    (here / "policy.json").write_text(json.dumps({"rules": []}), encoding="utf-8")
    (here / "handlers" / "get_order.py").write_text(HANDLER, encoding="utf-8")
    (here / "mcp_capabilities.json").write_text(
        json.dumps(
            {
                "gmail_inbox_triage": {
                    "server": "Google Gmail MCP server (Developer Preview)",
                    "tools": ["search_threads", "get_thread"],
                    "tables": ["gmail_messages", "gmail_threads"],
                }
            }
        ),
        encoding="utf-8",
    )
    for name, opening in OPENINGS.items():
        folder = here / "suite" / "scenarios" / name
        folder.mkdir(parents=True)
        persona = {"initial_message": opening} if opening else {}
        (folder / "scenario.json").write_text(
            json.dumps({"name": name, "persona": persona}), encoding="utf-8"
        )
    return here


def test_a_package_seals_the_catalogue_its_card_shows(tmp_path):
    here = _package(tmp_path / "corner_shop")
    digest = designed.build_environment(here, tmp_path / "environment")

    release = load_environment_release(tmp_path / "environment")
    assert release.digest == digest
    assert release.metadata["catalogue"] == {
        "title": "Corner Shop",
        # The first sentence of the first paragraph, its wrapped lines joined.
        "summary": "A small shop's returns desk.",
        "channel": "chat",
        # A business table only: not the runtime's own (clock, caller, actor), not the policy
        # table, not an underscore table, and not an app's.
        "records": {"orders": 3},
        "apps": [
            {
                "capability": "gmail_inbox_triage",
                "server": "Google Gmail MCP server (Developer Preview)",
                "tools": ["search_threads", "get_thread"],
                "records": 5,
            }
        ],
        "example_requests": ["Where is order 1002?", "I want to return order 1001."],
    }


def test_a_package_without_a_readme_or_apps_still_has_a_card(tmp_path):
    here = _package(tmp_path / "corner_shop")
    (here / "README.md").unlink()
    (here / "mcp_capabilities.json").unlink()
    designed.build_environment(here, tmp_path / "environment")

    card = load_environment_release(tmp_path / "environment").metadata["catalogue"]
    assert card["title"] == "Corner Shop" and card["summary"] == ""
    assert card["apps"] == []
    # With no app claiming them, the inbox tables are the world's own records.
    assert card["records"] == {"gmail_messages": 3, "gmail_threads": 2, "orders": 3}

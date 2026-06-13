"""Suite-wide fixtures.

The telemetry run ledger is always-on for real users; without an override the
test suite would append hundreds of rows to the developer's real ledger at
``~/.agent-learning/ledger/`` (and could leave a stale ``sync.cursor``).
Point every test at an ephemeral ledger directory instead. Tests that assert
ledger behaviour explicitly set ``AGENT_LEARNING_LEDGER_PATH`` themselves via
``monkeypatch``, which takes precedence over this session-scoped default.
"""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_run_ledger():
    if os.environ.get("AGENT_LEARNING_LEDGER_PATH"):
        yield
        return
    with tempfile.TemporaryDirectory(prefix="agent-learning-test-ledger-") as tmp:
        os.environ["AGENT_LEARNING_LEDGER_PATH"] = tmp
        try:
            yield
        finally:
            os.environ.pop("AGENT_LEARNING_LEDGER_PATH", None)

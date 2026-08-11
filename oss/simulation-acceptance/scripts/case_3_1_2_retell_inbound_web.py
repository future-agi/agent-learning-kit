#!/usr/bin/env python3
"""Case 3.1.2 — The robot customer CALLS your Retell agent over the internet.

Uses Retell's own web-call channel. No phone bill.

Before you run this:
  Nothing to run locally — Retell hosts your agent.
  No phone number needed.

This costs about two cents of AI usage. No phone call is made.

Run it with:
    python retell_inbound_web.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import run


# ==================================================================
#  FILL THIS IN — everything else is already configured for you
# ==================================================================

# From your Retell dashboard
RETELL_AGENT_ID = "paste-your-agent-id"

# So the simulator knows what it is talking to
RETELL_TARGET_SYSTEM_PROMPT = "paste your agent's system prompt here"

# ==================================================================


if __name__ == "__main__":
    sys.exit(
        run(
            "3.1.2",
            description="The robot customer CALLS your Retell agent over the internet",
            config={
                "RETELL_AGENT_ID": RETELL_AGENT_ID,
                "RETELL_TARGET_SYSTEM_PROMPT": RETELL_TARGET_SYSTEM_PROMPT,
            },
        )
    )

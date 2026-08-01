#!/usr/bin/env python3
"""Case 3.1.1 — The robot customer PHONES your Retell agent.

Your Retell agent receives a real phone call.

Before you run this:
  Nothing to run locally — Retell hosts your agent.

This places a REAL phone call and costs a few cents.

Run it with:
    python retell_inbound_phone.py
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

# The phone number attached to that agent
RETELL_TARGET_PHONE_NUMBER = "+1XXXXXXXXXX"

# So the simulator knows what it is talking to
RETELL_TARGET_SYSTEM_PROMPT = "paste your agent's system prompt here"

# ==================================================================


if __name__ == "__main__":
    sys.exit(
        run(
            "3.1.1",
            description="The robot customer PHONES your Retell agent",
            config={
                "RETELL_AGENT_ID": RETELL_AGENT_ID,
                "RETELL_TARGET_PHONE_NUMBER": RETELL_TARGET_PHONE_NUMBER,
                "RETELL_TARGET_SYSTEM_PROMPT": RETELL_TARGET_SYSTEM_PROMPT,
            },
        )
    )

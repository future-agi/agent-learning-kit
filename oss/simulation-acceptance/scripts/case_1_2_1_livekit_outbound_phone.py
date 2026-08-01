#!/usr/bin/env python3
"""Case 1.2.1 — Your LiveKit agent PHONES the robot customer.

Your agent places a real outgoing call and speaks first.

Before you run this:
  Your agent must be RUNNING, started with these two extra settings:
    REFERENCE_AGENT_OUTBOUND_SIP_ENABLED=true
    REFERENCE_AGENT_OUTBOUND_SIP_ALLOWED_NUMBER=<LIVEKIT_INBOUND_DID from .env>
  The second is a safety fuse: your agent can dial nothing else.

This places a REAL phone call and costs a few cents.

Run it with:
    python livekit_outbound_phone.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import run


# ==================================================================
#  FILL THIS IN — everything else is already configured for you
# ==================================================================

# The name your agent registers with LiveKit
LIVEKIT_TARGET_AGENT_NAME = "your-agent-worker-name"

# So the simulator knows what it is talking to
LIVEKIT_TARGET_SYSTEM_PROMPT = "paste your agent's system prompt here"

# ==================================================================


if __name__ == "__main__":
    sys.exit(
        run(
            "1.2.1",
            description="Your LiveKit agent PHONES the robot customer",
            config={
                "LIVEKIT_TARGET_AGENT_NAME": LIVEKIT_TARGET_AGENT_NAME,
                "LIVEKIT_TARGET_SYSTEM_PROMPT": LIVEKIT_TARGET_SYSTEM_PROMPT,
            },
        )
    )

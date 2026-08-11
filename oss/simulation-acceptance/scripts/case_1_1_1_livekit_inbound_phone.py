#!/usr/bin/env python3
"""Case 1.1.1 — The robot customer PHONES your LiveKit agent.

Your agent receives a real phone call from our simulated customer.

Before you run this:
  Your agent must be RUNNING (see setup guide, step 4b).
  This case creates no routing of its own: the number above must ALREADY
  reach your agent. If you have a working phone agent, you already have this.

This places a REAL phone call and costs a few cents.

Run it with:
    python livekit_inbound_phone.py
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

# Your agent's phone number, in +1... format
LIVEKIT_TARGET_PHONE_NUMBER = "+1XXXXXXXXXX"

# So the simulator knows what it is talking to
LIVEKIT_TARGET_SYSTEM_PROMPT = "paste your agent's system prompt here"

# ==================================================================


if __name__ == "__main__":
    sys.exit(
        run(
            "1.1.1",
            description="The robot customer PHONES your LiveKit agent",
            config={
                "LIVEKIT_TARGET_AGENT_NAME": LIVEKIT_TARGET_AGENT_NAME,
                "LIVEKIT_TARGET_PHONE_NUMBER": LIVEKIT_TARGET_PHONE_NUMBER,
                "LIVEKIT_TARGET_SYSTEM_PROMPT": LIVEKIT_TARGET_SYSTEM_PROMPT,
            },
        )
    )

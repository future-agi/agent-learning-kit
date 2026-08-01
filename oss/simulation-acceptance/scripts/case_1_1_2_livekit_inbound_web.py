#!/usr/bin/env python3
"""Case 1.1.2 — The robot customer CALLS your LiveKit agent over the internet.

No phone network, no phone bill. The easiest place to start.

Before you run this:
  Your agent must be RUNNING (see setup guide, step 4b).

This costs about two cents of AI usage. No phone call is made.

Run it with:
    python livekit_inbound_web.py
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
            "1.1.2",
            description="The robot customer CALLS your LiveKit agent over the internet",
            config={
                "LIVEKIT_TARGET_AGENT_NAME": LIVEKIT_TARGET_AGENT_NAME,
                "LIVEKIT_TARGET_SYSTEM_PROMPT": LIVEKIT_TARGET_SYSTEM_PROMPT,
            },
        )
    )

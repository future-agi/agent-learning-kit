#!/usr/bin/env python3
"""Case 1.2.2 — Your LiveKit agent STARTS the conversation over the internet.

Your agent speaks first. No phone network, no phone bill.

Before you run this:
  Your agent must be RUNNING and configured to speak first,
  e.g. REFERENCE_AGENT_INITIAL_GREETING="Hello, this is support calling."

This costs about two cents of AI usage. No phone call is made.

Run it with:
    python livekit_outbound_web.py
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
            "1.2.2",
            description="Your LiveKit agent STARTS the conversation over the internet",
            config={
                "LIVEKIT_TARGET_AGENT_NAME": LIVEKIT_TARGET_AGENT_NAME,
                "LIVEKIT_TARGET_SYSTEM_PROMPT": LIVEKIT_TARGET_SYSTEM_PROMPT,
            },
        )
    )

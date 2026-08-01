#!/usr/bin/env python3
"""Case 2.1.2 — The robot customer CALLS your Vapi agent over the internet.

Uses Vapi's own web-call channel. No phone bill. The most reliable case.

Before you run this:
  Nothing to run locally — Vapi hosts your assistant.
  No phone number needed.

This costs about two cents of AI usage. No phone call is made.

Run it with:
    python vapi_inbound_web.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import run


# ==================================================================
#  FILL THIS IN — everything else is already configured for you
# ==================================================================

# From your Vapi dashboard
VAPI_ASSISTANT_ID = "paste-your-assistant-id"

# So the simulator knows what it is talking to
VAPI_TARGET_SYSTEM_PROMPT = "paste your assistant's system prompt here"

# ==================================================================


if __name__ == "__main__":
    sys.exit(
        run(
            "2.1.2",
            description="The robot customer CALLS your Vapi agent over the internet",
            config={
                "VAPI_ASSISTANT_ID": VAPI_ASSISTANT_ID,
                "VAPI_TARGET_SYSTEM_PROMPT": VAPI_TARGET_SYSTEM_PROMPT,
            },
        )
    )

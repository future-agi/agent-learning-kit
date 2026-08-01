#!/usr/bin/env python3
"""Case 2.1.1 — The robot customer PHONES your Vapi agent.

Your Vapi assistant receives a real phone call.

Before you run this:
  Nothing to run locally — Vapi hosts your assistant.

This places a REAL phone call and costs a few cents.

Run it with:
    python vapi_inbound_phone.py
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

# The phone number attached to that assistant
VAPI_TARGET_PHONE_NUMBER = "+1XXXXXXXXXX"

# So the simulator knows what it is talking to
VAPI_TARGET_SYSTEM_PROMPT = "paste your assistant's system prompt here"

# ==================================================================


if __name__ == "__main__":
    sys.exit(
        run(
            "2.1.1",
            description="The robot customer PHONES your Vapi agent",
            config={
                "VAPI_ASSISTANT_ID": VAPI_ASSISTANT_ID,
                "VAPI_TARGET_PHONE_NUMBER": VAPI_TARGET_PHONE_NUMBER,
                "VAPI_TARGET_SYSTEM_PROMPT": VAPI_TARGET_SYSTEM_PROMPT,
            },
        )
    )

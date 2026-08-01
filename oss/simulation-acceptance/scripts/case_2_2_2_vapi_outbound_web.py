#!/usr/bin/env python3
"""Case 2.2.2 — Your Vapi agent STARTS the conversation over the internet.

Your assistant speaks first. No phone bill.

Before you run this:
  Your assistant needs the endCall tool enabled in the Vapi dashboard,
  otherwise it never hangs up and the test times out.

This costs about two cents of AI usage. No phone call is made.

Run it with:
    python vapi_outbound_web.py
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
            "2.2.2",
            description="Your Vapi agent STARTS the conversation over the internet",
            config={
                "VAPI_ASSISTANT_ID": VAPI_ASSISTANT_ID,
                "VAPI_TARGET_SYSTEM_PROMPT": VAPI_TARGET_SYSTEM_PROMPT,
            },
        )
    )

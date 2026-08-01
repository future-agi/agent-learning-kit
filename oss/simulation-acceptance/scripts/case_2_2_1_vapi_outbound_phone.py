#!/usr/bin/env python3
"""Case 2.2.1 — Your Vapi agent PHONES the robot customer.

Your assistant places a real outgoing call and speaks first.

Before you run this:
  VAPI_PHONE_NUMBER_ID must be a TWILIO-BACKED number in Vapi.
  Vapi's own free numbers accept the request then silently cancel the call.

This places a REAL phone call and costs a few cents.

Run it with:
    python vapi_outbound_phone.py
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

# The Vapi phone-number ID it should call FROM
VAPI_PHONE_NUMBER_ID = "paste-your-phone-number-id"

# So the simulator knows what it is talking to
VAPI_TARGET_SYSTEM_PROMPT = "paste your assistant's system prompt here"

# ==================================================================


if __name__ == "__main__":
    sys.exit(
        run(
            "2.2.1",
            description="Your Vapi agent PHONES the robot customer",
            config={
                "VAPI_ASSISTANT_ID": VAPI_ASSISTANT_ID,
                "VAPI_PHONE_NUMBER_ID": VAPI_PHONE_NUMBER_ID,
                "VAPI_TARGET_SYSTEM_PROMPT": VAPI_TARGET_SYSTEM_PROMPT,
            },
        )
    )

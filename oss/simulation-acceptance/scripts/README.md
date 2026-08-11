# Test your voice agent — one script per case

Ten ready-to-run scripts. **Open the one you want, fill in two or three details about your agent at the top, and run it.** Everything else — the simulator, the room, phone trunks, caller numbers — is already configured.

## Pick your script

| If your agent is on… | and you want to test… | run |
| --- | --- | --- |
| **LiveKit** | a customer calling in, over the internet | `case_1_1_2_livekit_inbound_web.py` |
| | your agent calling out, over the internet | `case_1_2_2_livekit_outbound_web.py` |
| | a customer phoning in, real phone call | `case_1_1_1_livekit_inbound_phone.py` |
| | your agent phoning out, real phone call | `case_1_2_1_livekit_outbound_phone.py` |
| **Vapi** | a customer calling in, over the internet | `case_2_1_2_vapi_inbound_web.py` ← *start here* |
| | your agent calling out, over the internet | `case_2_2_2_vapi_outbound_web.py` |
| | a customer phoning in, real phone call | `case_2_1_1_vapi_inbound_phone.py` |
| | your agent phoning out, real phone call | `case_2_2_1_vapi_outbound_phone.py` |
| **Retell** | a customer calling in, over the internet | `case_3_1_2_retell_inbound_web.py` ← *start here* |
| | a customer phoning in, real phone call | `case_3_1_1_retell_inbound_phone.py` |

New to this? Start with the **internet** version for your provider. It needs the least setup, costs about two cents, and proves your wiring works before you spend money on phone calls.

## How to run one

```bash
# 1. Open the script and fill in the block marked FILL THIS IN
# 2. Run it
python case_2_1_2_vapi_inbound_web.py
```

Each script checks your configuration first without placing a call, then runs the conversation:

```
  Case 2.1.2 — The robot customer CALLS your Vapi agent over the internet
  [1/2] Checking configuration...
  Configuration OK.
  [2/2] Running the conversation...

  PASSED — the conversation completed.
```

If you haven't filled something in, it tells you exactly which field is missing rather than failing halfway through.

## What you get

Transcripts and audio recordings of both sides land in `artifacts/simulation-acceptance/`. Playing one back is the quickest way to understand what the test actually did.

In the transcript, `assistant` is **your agent** and `user` is **our robot customer**.

## Before you start

- Create `.env.acceptance` at the repo root with your shared credentials — you only do this once. To keep credentials outside the repo, put the file anywhere and set `ACCEPTANCE_ENV_FILE` to its path.
- **Testing a LiveKit agent?** Your agent must be running in another terminal — see the setup guide. Agents on Vapi or Retell need nothing running locally; their cloud hosts them.
- **Phone cases cost real money** — roughly ten cents a conversation — and dial a real number. Check the number you entered.

## If a test fails

About **1 in 10 voice runs stalls** for reasons unrelated to your agent. Run it again before investigating. If it fails three times, the `failure` field in the output says why, and the setup guide has a troubleshooting table.

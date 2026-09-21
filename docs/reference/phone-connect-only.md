# Connect-only phone agents (Others)

Use this path when an existing voice agent answers a public phone number and has no Vapi/Retell agent ID. ALK does not clone, modify, or rewire that agent.

In the UI, select **Others (phone number)**, enter the agent's E.164 number (`+` and country code), paste its system prompt, choose the scenario count, then run preflight. A repository upload is optional. The prompt is the supplied behavioral description for scenario authoring; ALK cannot verify that the live agent actually uses it.

The platform operator must provision `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `SIP_OUTBOUND_TRUNK_ID`, and `SIP_OUTBOUND_FROM_NUMBER` on both the API and simulation-runner worker. The caller ID must be E.164 and authorized on that trunk. Preflight reports missing platform telephony configuration rather than asking the customer for SIP credentials. Each scenario places a real outbound phone call and may incur carrier charges; only test numbers you control or have permission to call.

Audio, transcripts, and recordings use the usual voice-simulation path. The agent's existing HTTP tools still call their existing endpoints. Without separate integration or source-side routing, ALK cannot redirect those calls into the isolated world or claim their state changes as tool evidence. Source-free authoring treats the supplied prompt as unverified input and does not invent tool schemas.

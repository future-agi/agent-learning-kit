# Connect-only phone agents (Others)

## Vapi and Retell telephony

Select Vapi or Retell, **Use existing agent**, then **Call connection → Phone call (telephony)**. Supply the existing agent's E.164 phone number. Either provide both the provider API key and agent ID to fetch its definition, or leave both empty and paste the system prompt. Web calls remain the default. Telephony is not available in clone/import mode or native Retell chat.

For API clients, use `agent.mode = "connect_only"` with `agent.config.phone_number`. With credentials, retain `connector = "vapi"` and `config.assistant_id`, or `connector = "retell"` and `config.agent_id`; supply the respective `VAPI_API_KEY` or `RETELL_API_KEY` through a run-scoped `secret_refs` reference. Without credentials/ID, supply `config.target_system_prompt`; the platform normalizes this into the existing `phone` connector. No source upload is required for either option.

The number must reach the intended agent; fetching an agent definition does not verify number ownership or association. Calls use the platform SIP trunk, not Vapi/Retell web-call APIs. Existing tool endpoints stay unchanged; this path does not automatically collect provider-side tool traces.

Use this path when an existing voice agent answers a public phone number and has no Vapi/Retell agent ID. ALK does not clone, modify, or rewire that agent.

In the UI, select **Others (phone number)**, enter the agent's E.164 number (`+` and country code), paste its system prompt, choose the scenario count, then run preflight. A repository upload is optional. The prompt is the supplied behavioral description for scenario authoring; ALK cannot verify that the live agent actually uses it.

The platform operator must provision the existing agent-definition outbound telephony settings (`LIVEKIT_OUTBOUND_TRUNK_ID` and `PSTN_CALLER_NUMBER`) plus `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` on both the API and simulation-runner worker. The legacy `SIP_OUTBOUND_TRUNK_ID` and `SIP_OUTBOUND_FROM_NUMBER` names remain accepted as a fallback. The caller ID must be E.164 and authorized on the outbound trunk. Preflight reports missing platform telephony configuration rather than asking the customer for SIP credentials. Each scenario places a real outbound phone call and may incur carrier charges; only test numbers you control or have permission to call.

Audio, transcripts, and recordings use the usual voice-simulation path. The agent's existing HTTP tools still call their existing endpoints. Without separate integration or source-side routing, ALK cannot redirect those calls into the isolated world or claim their state changes as tool evidence. Source-free authoring treats the supplied prompt as unverified input and does not invent tool schemas.

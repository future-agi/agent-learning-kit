---
name: voice-hosted-platform
description: "A platform runs the voice agent and calls YOU. Evidence: a Vapi or Retell API key, assistant id or pathway id in configuration; webhook or function-call HTTP handlers in the repository with no agent process anywhere; docs describing an assistant configured in someone's dashboard. You cannot run their worker; you build the tool service their webhooks hit. NOT this file when the repository ships its own LiveKit worker (voice-livekit.md)."
---

# Voice agents hosted by a platform

> **Selection check.** You are in the right file if the repository serves webhooks but contains no agent process, and the agent itself lives in a platform account. If you found a runnable worker in the repository, stop and read `voice-livekit.md` instead.

The platform runs the agent and calls the service you expose. Build the real tool service and its
dependencies; do not recreate the platform's conversation runtime, audio stack or tool dispatcher.

## Prove reachability before building scenarios

Read the assistant configuration and its repository together. Establish:

- The exact webhook or tool URL configuration seam.
- Authentication expected by the service and how the platform sends it.
- The ingress path from the hosted platform to this environment.
- Any callback, status or transcript path required to observe a completed call.

The service must be reachable from the platform, not merely from inside the sandbox. If ingress,
credentials or an update path for the platform configuration is unavailable, state the missing seam
and stop. A healthy local service that cannot receive platform calls is not an environment for this
agent.

## Build only the submitted service

Use the repository's Compose file, Dockerfile, migrations, lockfile and seed process. Give its
real datastore an isolated baseline and change only documented configuration values needed to point
the service at it. Preserve request and response schema exactly. Do not provide a replacement
webhook handler, synthetic success response or a local imitation of the hosted assistant.

Checks should verify durable business state and the service's own refusal paths. They should not
claim that a remote platform tool executed until a real platform call and evidence record prove it.

## Keep platform concerns separate

The platform owns audio, interruption, turn timing and conversation lifecycle. Describe those in
the simulator and scenario only where its supported runner can actually drive them. The world owns
the tool service, its data and the records that show what the service did.
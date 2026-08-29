---
name: voice-bland
description: "Use when the agent under test is a Bland assistant reached through Bland's own API rather than a worker you can run. Signs you are here: a Bland API key or pathway id in the submitted configuration, no LiveKit worker in the repository, and tool calls that leave as webhooks to a URL the repository serves. Do NOT use when the repository ships its own LiveKit worker (voice-livekit.md), and do NOT use for Vapi or Retell, which have their own file."
---

# Bland assistants

Stub. Bland places the call and invokes your webhook; you never run their worker. The shape is the
same as `voice-hosted-platform.md`: build the real tool service the assistant's webhooks target,
expose it on a stable ingress, and point the assistant at it. Read that file first, then add only
what Bland does differently.

This file exists to demonstrate that supporting a new platform is a markdown file and nothing else.

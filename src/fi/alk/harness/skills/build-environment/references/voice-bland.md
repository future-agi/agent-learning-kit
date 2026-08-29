---
name: voice-bland
description: "Specifically a Bland assistant. Evidence: a Bland API key, a Bland pathway id, or api.bland.ai in configuration or code, with no agent process in the repository. Read voice-hosted-platform.md first for the general shape; this file covers only what Bland does differently. NOT this file for Vapi or Retell."
---

# Bland assistants

> **Selection check.** You are in the right file only if you found Bland credentials specifically. For any other hosted platform, read `voice-hosted-platform.md`.

Stub. Bland places the call and invokes your webhook; you never run their worker. The shape is the
same as `voice-hosted-platform.md`: build the real tool service the assistant's webhooks target,
expose it on a stable ingress, and point the assistant at it. Read that file first, then add only
what Bland does differently.

This file exists to demonstrate that supporting a new platform is a markdown file and nothing else.
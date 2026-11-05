# SSE transport implementation

- [x] Inspect current HTTP transport, runtime contract, callers, and existing SSE/tool behavior.
- [x] Pin work to current `origin/dev` in an isolated worktree.
- [x] Select the architecture after the three-candidate arena and cross-judge.
- [x] Implement the smallest validated live-SSE contract without changing buffered HTTP behavior.
- [x] Add observable tests for early completion, delta/cumulative/final assembly, disconnects, bounds, cancellation, session cookies, and tool evidence.
- [x] Add a pinned open-source agent qualification fixture and document its credential requirement and split-tool-event gap.
- [x] Run focused tests, the relevant broader suite, formatting, lint, and the offline reference verifier.
- [x] Review the final diff for compatibility, security, and unnecessary surface.

Live qualification still required outside this implementation unit:

- [ ] Run the pinned AG-UI CrewAI example with `OPENAI_API_KEY` and retain the two-turn wire trace.
- [ ] Qualify split `TOOL_CALL_*` / `TOOL_CALL_RESULT` pairing before claiming upstream tool-evidence support.

Skipped for this unit unless a failing acceptance test requires them:

- Public token-by-token subscription API; callers still receive one completed `AgentResponse`.
- Automatic reconnect or `Last-Event-ID` replay; retries can duplicate agent/tool side effects.
- New dependencies; `httpx` is already installed.

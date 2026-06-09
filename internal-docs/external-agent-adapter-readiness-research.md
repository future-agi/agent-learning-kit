# External Agent Adapter Readiness

The external-agent adapter gate proves Agent Learning Kit can optimize an
already-running target agent across an HTTP/OpenAI-compatible boundary without
turning that target into an in-process framework shim.

Release-check runs `examples/sdk_external_http_agent_optimization.py` with a
temporary local endpoint. The local endpoint implements `/v1/chat/completions`,
requires bearer auth from `AGENT_LEARNING_SDK_EXTERNAL_HTTP_AGENT_KEY`, and only
returns the `external_agent_status` tool call when the optimizer selects the
OpenAI-compatible candidate that includes the tool schema. This keeps the gate
deterministic and local while still exercising the same protocol, auth, trace,
and tool-call evidence required for user-owned external agents.

The release bar is:

- Search complete adapter candidates, not prompt fragments:
  `raw_http_agent_learning_payload`,
  `openai_compatible_without_tool_schema`, and
  `verified_openai_compatible_tools`.
- Select `verified_openai_compatible_tools` with `agent.type=openai_compatible`,
  `protocol=openai_chat`, `include_tools=true`, and only an `agent` patch.
- Preserve the authorization boundary: traces expose the env var name and
  `auth.redacted=true`, never the secret value.
- Preserve OpenAI-compatible tool-call evidence: request and response tool
  counts are nonzero, `external_agent_status` is executed, and metrics such as
  `tool_selection_accuracy`, `task_completion`, `secret_leakage`, and
  `tool_argument_schema` pass at 1.0.
- Emit the normal optimization report and action catalog so the artifact can be
  used with `agent-learn report`, `agent-learn actions`, and promotion/rerun
  flows.

Sources used for the gate:

- https://arxiv.org/abs/2605.11378
- https://arxiv.org/abs/2602.03238
- https://arxiv.org/abs/2603.15483
- https://arxiv.org/abs/2605.10912
- https://arxiv.org/abs/2604.16762
- https://arxiv.org/abs/2604.11790
- https://arxiv.org/abs/2603.30016
- https://arxiv.org/abs/2604.04820
- https://developers.openai.com/api/docs/guides/function-calling
- https://developers.openai.com/api/reference/overview#authentication

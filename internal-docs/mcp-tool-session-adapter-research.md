# MCP Tool Session Adapter Research Note

Date: 2026-06-08

## Sources Checked

- Model Context Protocol 2025-06-18 server tools specification:
  https://modelcontextprotocol.io/specification/2025-06-18/server/tools
- Model Context Protocol 2025-06-18 server resources specification:
  https://modelcontextprotocol.io/specification/2025-06-18/server/resources
- OpenAI Agents SDK MCP integration docs:
  https://openai.github.io/openai-agents-python/mcp/

## Adapter Implications

MCP makes a tool server a first-class runtime boundary, not just an LLM
function-call list. A local adapter must preserve at least four pieces of
evidence:

- The server/session identity, so a run can distinguish which MCP server emitted
  tools, calls, resources, and results.
- Tool schema evidence from `tools/list` or equivalent exported schemas, so
  evaluators can check whether required tools were available before a call.
- Tool invocation evidence from `tools/call` request/result/error pairs, so
  ordinary tool-selection and tool-outcome metrics can score the MCP session.
- Resource evidence from MCP resources or templates, so retrieval/context use is
  visible in the same trace artifact.

The OpenAI Agents SDK supports MCP servers over stdio and streamable HTTP, and
the same adapter contract should not depend on either transport during local
release checks. The local surface should therefore accept exported JSON-RPC
records and fixture-style `{tools, calls, resources}` payloads instead of
starting an MCP process or contacting a remote server.

## Implemented Contract

The generic framework adapter now treats local outputs as MCP tool sessions when
they carry explicit MCP fields such as `mcp_tool_session`, `mcp_events`,
`mcp_tools`, `mcp_resources`, `mcp_calls`, or `mcp_tool_calls`, or when a payload
is explicitly marked as MCP and includes protocol fields such as `tools`,
`calls`, `events`, `requests`, `responses`, or `resources`.

Those outputs normalize into:

- `mcp_tool_session` state with span, schema, resource, call, result, error,
  server, session, and tool summaries.
- A `trace` artifact with `metadata.kind == "mcp_tool_session"`.
- `mcp_server`, `mcp_tool_schema`, `mcp_resource`, `mcp_tool_call`,
  `mcp_tool_result`, `mcp_tool_error`, and final `mcp_tool_session` events.
- Ordinary `AgentResponse.tool_calls` and `tool_responses`, so existing
  `tool_selection_accuracy`, tool outcome, and runtime-contract gates can score
  MCP sessions without MCP-specific report wiring.

The cookbook in `examples/sdk_framework_adapter_mcp_tool_session.py` covers the
strongest local path: adapter discovery selects `execute_task(dict)`, the
adapter emits a local MCP session with `tools/list`, two `tools/call`
request/result pairs, and a resource, and the generated eval config requires
the resulting tools, events, trace artifact, and `mcp_tool_session` state.

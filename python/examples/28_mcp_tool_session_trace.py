"""
Score MCP tool-session trace evidence locally.

Use this after an MCP tools/list and tools/call export has been normalized into
a `framework_trace` artifact. The evaluator checks tool-session trace coverage,
argument schema conformance, and expected tool outcomes without a model or API
key.
"""

from fi.evals.metrics.agents import evaluate_agent_report


report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Inspect the MCP session."},
                {"role": "assistant", "content": "MCP session inspected."},
            ],
            "artifacts": [
                {
                    "type": "trace",
                    "metadata": {"kind": "framework_trace", "framework": "mcp"},
                    "data": {
                        "kind": "framework_trace",
                        "framework": "mcp",
                        "signals": [
                            "tool",
                            "mcp_tool_schema",
                            "mcp_tool_call",
                            "mcp_tool_result",
                        ],
                        "spans": [
                            {
                                "name": "MCP tool schema search_order",
                                "type": "mcp_tool_schema",
                                "tool_name": "search_order",
                                "signals": ["tool", "mcp_tool_schema", "tool_schema"],
                                "attributes": {
                                    "mcp.tool.name": "search_order",
                                    "mcp.tool.input_schema": {
                                        "type": "object",
                                        "properties": {
                                            "order_id": {"type": "string"},
                                        },
                                        "required": ["order_id"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            {
                                "name": "MCP tool result search_order",
                                "type": "mcp_tool_result",
                                "tool_name": "search_order",
                                "input": {"order_id": "ord_123"},
                                "output": {"resolved": True, "status": "found"},
                                "signals": [
                                    "tool",
                                    "mcp_tool_call",
                                    "mcp_tool_result",
                                    "tool_result",
                                ],
                                "attributes": {
                                    "mcp.tool.name": "search_order",
                                    "success": True,
                                },
                            },
                        ],
                    },
                }
            ],
        }
    ]
}

result = evaluate_agent_report(
    report,
    config={
        "required_framework_trace": [
            "tool",
            "mcp_tool_schema",
            "mcp_tool_call",
            "mcp_tool_result",
        ],
        "expected_tool_outcomes": {
            "search_order": {
                "success": True,
                "result": {"resolved": True, "status": "found"},
            }
        },
    },
    threshold=0.9,
)

metrics = result.summary["metric_averages"]

print("score:", result.score)
print("passed:", result.passed)
print("framework_trace_coverage:", metrics.get("framework_trace_coverage"))
print("tool_argument_schema:", metrics.get("tool_argument_schema"))
print("tool_outcome:", metrics.get("tool_outcome"))

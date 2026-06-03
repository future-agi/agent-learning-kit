"""
Score orchestration graph traces locally.

This catches workflow-level failures that raw span coverage can miss: missing
routes, disabled retries, unrecovered errors, terminal failures, and graph
latency/cost budget violations.
"""

from fi.evals.metrics.agents import evaluate_agent_report


trace = {
    "kind": "orchestration_trace",
    "framework": "langgraph",
    "nodes": [
        {"id": "triage_agent", "name": "triage_agent", "signals": ["agent", "node"]},
        {"id": "policy_agent", "name": "policy_agent", "signals": ["agent", "node"]},
        {"id": "refund_tool", "name": "refund_tool", "signals": ["tool", "node"]},
    ],
    "edges": [
        {"from": "triage_agent", "to": "policy_agent", "type": "handoff", "signals": ["route", "handoff"]},
        {"from": "policy_agent", "to": "refund_tool", "type": "route", "signals": ["route", "tool"]},
    ],
    "steps": [
        {
            "id": "workflow",
            "name": "invoke_workflow refund_graph",
            "type": "workflow",
            "node": "refund_graph",
            "status": "success",
            "latency_ms": 8,
            "signals": ["workflow", "latency"],
        },
        {
            "id": "route_policy",
            "name": "handoff triage to policy",
            "type": "handoff",
            "node": "triage_agent",
            "route_from": "triage_agent",
            "route_to": "policy_agent",
            "status": "success",
            "latency_ms": 12,
            "signals": ["route", "handoff", "latency"],
        },
        {
            "id": "policy_error",
            "name": "policy_agent tool timeout",
            "type": "tool",
            "node": "policy_agent",
            "status": "error",
            "error": {"message": "rate limit", "recoverable": True},
            "latency_ms": 40,
            "signals": ["tool", "error", "latency"],
        },
        {
            "id": "policy_retry",
            "name": "policy_agent retry succeeded",
            "type": "tool",
            "node": "policy_agent",
            "status": "success",
            "attempt": 2,
            "recovered": True,
            "latency_ms": 35,
            "cost": {"total_tokens": 80},
            "signals": ["tool", "retry", "recovered", "latency", "cost"],
        },
        {
            "id": "refund_tool",
            "name": "execute_tool issue_refund",
            "type": "tool",
            "node": "refund_tool",
            "route_from": "policy_agent",
            "route_to": "refund_tool",
            "status": "success",
            "latency_ms": 30,
            "signals": ["tool", "route", "latency"],
        },
    ],
    "signals": ["workflow", "node", "route", "handoff", "tool", "retry", "recovered", "latency", "cost", "state"],
    "summary": {
        "retry_count": 1,
        "failure_count": 1,
        "recovered_failures": 1,
        "total_latency_ms": 125,
        "total_cost": 80,
        "terminal_status": "success",
    },
    "state": {"case": {"status": "resolved"}},
}

report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Resolve refund order ord_123."},
                {
                    "role": "assistant",
                    "content": "The workflow recovered from a transient policy lookup error and issued the refund.",
                },
            ],
            "artifacts": [
                {
                    "type": "trace",
                    "metadata": {"kind": "orchestration_trace", "framework": "langgraph"},
                    "data": trace,
                }
            ],
        }
    ]
}

result = evaluate_agent_report(
    report,
    config={
        "required_orchestration_trace": [
            "workflow",
            "node",
            "route",
            "handoff",
            "tool",
            "retry",
            "recovered",
            "latency",
            "cost",
            "state",
        ],
        "orchestration_trace_quality": {
            "required_nodes": ["triage_agent", "policy_agent", "refund_tool"],
            "forbidden_nodes": ["manual_escalation"],
            "required_step_types": ["workflow", "tool", "retry"],
            "expected_routes": [
                {"from": "triage_agent", "to": "policy_agent", "type": "handoff"},
                {"from": "policy_agent", "to": "refund_tool"},
            ],
            "min_retry_count": 1,
            "require_recovered_errors": True,
            "expected_recovered_errors": [{"node": "policy_agent"}],
            "max_total_latency_ms": 150,
            "max_step_latency_ms": 50,
            "max_total_cost": 100,
            "max_error_count": 1,
            "required_terminal_status": "success",
            "expected_state": {"case": {"status": "resolved"}},
        },
        "metric_weights": {
            "orchestration_trace_coverage": 4.0,
            "orchestration_flow_quality": 5.0,
        },
    },
    threshold=0.85,
)

metrics = result.summary["metric_averages"]

print("score:", result.score)
print("passed:", result.passed)
print("orchestration_trace_coverage:", metrics.get("orchestration_trace_coverage"))
print("orchestration_flow_quality:", metrics.get("orchestration_flow_quality"))

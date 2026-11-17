"""The frameworks probe traces only the text of a non-string AgentResponse content."""

import asyncio
import json

from text_of_cases import ANSWER, _blocks

from fi.simulate.agent.wrapper import AgentResponse, AgentWrapper


def test_a_non_string_agent_response_is_normalized_and_keeps_its_fields():
    from fi.simulate.agent.frameworks import _run_probe_case

    class Agent(AgentWrapper):
        async def call(self, input):
            return AgentResponse.model_construct(
                content=_blocks(),
                tool_calls=[{"name": "lookup", "arguments": {}}],
                artifacts=[],
                events=[],
            )

    result = asyncio.run(
        _run_probe_case(
            Agent(),
            "generic",
            {"id": "blocks", "input": "hi"},
            index=1,
            trace_runtime=False,
            contract={},
        )
    )
    assert result["response"]["content"] == ANSWER
    assert result["response"]["tool_names"] == ["lookup"]
    payload = json.dumps(result, default=str)
    assert "sig-abc" not in payload and "plan the reply" not in payload

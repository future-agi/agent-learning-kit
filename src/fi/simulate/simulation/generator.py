from typing import List
from pydantic import BaseModel, Field
from fi.simulate.agent.definition import AgentDefinition
from fi.simulate.simulation.models import Persona

try:
    from livekit.plugins import openai
    from livekit.agents.llm.chat_context import ChatContext
except ImportError:
    # LiveKit is an optional dependency. In cloud-only usage, we silently skip it.
    openai = None
    ChatContext = None
import json

class ScenarioGenerator:
    """
    Uses an LLM to automatically generate a list of test case personas.
    """

    def __init__(self, agent_definition: AgentDefinition):
        self._agent_definition = agent_definition
        self._llm = openai.LLM()

    async def generate(self, topic: str, num_personas: int) -> List[Persona]:
        """
        Generates a list of personas based on a high-level topic.
        """
        prompt = self._create_generation_prompt(topic, num_personas)
        
        # Use chat() with a ChatContext, request JSON response format
        chat_ctx = ChatContext.empty()
        chat_ctx.add_message(role="user", content=prompt)
        # Do not force response_format; rely on prompt to return strict JSON
        stream = self._llm.chat(chat_ctx=chat_ctx)
        # Collect full text
        text = ""
        async for chunk in stream.to_str_iterable():
            text += chunk
        print("Scenario Generated:\n" + text)
        
        try:
            # Try direct parse; if it fails, attempt to extract fenced JSON
            try:
                generated_data = json.loads(text)
            except Exception:
                s = text.strip()
                if "```" in s:
                    parts = s.split("```")
                    for p in parts:
                        ps = p.strip()
                        if ps.startswith("{") and ps.endswith("}"):
                            s = ps
                            break
                generated_data = json.loads(s)
            personas = [Persona(**p) for p in generated_data["personas"]]
            return personas
        except (json.JSONDecodeError, KeyError) as e:
            raise ValueError(f"Failed to parse generated scenarios: {e}\nRaw response: {text}")

    def _create_generation_prompt(self, topic: str, num_personas: int) -> str:
        agent_context = self._agent_definition.system_prompt or self._agent_definition.description or ""
        
        return f"""
        You are a creative test case designer for voice AI agents. Your task is to generate {num_personas} diverse and realistic test case personas for an AI agent with the following description:
        ---
        AGENT DESCRIPTION: {agent_context}
        ---

        The user wants to generate scenarios related to the following topic: "{topic}".

        For each persona, you must generate:
        1. A detailed `persona` object (e.g., {{ "name": "John", "age": 45, "mood": "impatient", "background": "Is a busy executive" }}).
        2. A concise `situation` string describing the reason for their call.
        3. A clear `outcome` string describing the ideal resolution of the conversation from the user's perspective.

        Return your response as a single JSON object with a key "personas", which is a list of the generated persona objects. Do not include any other text or formatting.
        """

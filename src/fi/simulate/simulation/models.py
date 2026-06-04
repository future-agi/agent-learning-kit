from typing import List, Dict, Any, Union, Optional
from pydantic import BaseModel, Field, validator
import pandas as pd
import json
from fi.simulate.agent.wrapper import SimulationArtifact, SimulationEvent

class Persona(BaseModel):
    """
    A single test case defining a customer persona, situation, and desired outcome.
    """
    persona: Dict[str, Any] = Field(..., description="Characteristics of the simulated customer (e.g., name, age, communication_style).")
    situation: str = Field(..., description="The context or reason for the customer's call.")
    outcome: str = Field(..., description="The desired goal or resolution for the conversation.")

class Scenario(BaseModel):
    """
    Defines a collection of test cases for a simulation.
    """
    name: str = Field(..., description="A unique name for the scenario.")
    description: Optional[str] = Field(None, description="A brief description of what this scenario tests.")
    dataset: List[Persona] = Field(..., description="A list of personas defining the test cases.")

    @validator('dataset', pre=True)
    def load_dataset(cls, v: Union[List[Dict], str]) -> List[Dict]:
        if isinstance(v, str):
            if v.endswith('.csv'):
                return pd.read_csv(v).to_dict('records')
            elif v.endswith('.json'):
                with open(v, 'r') as f:
                    return json.load(f)
            else:
                raise ValueError("Unsupported file type for dataset. Please use .csv or .json.")
        return v

class TestCaseResult(BaseModel):
    """
    Represents the result of a single test case.
    """
    persona: Persona = Field(..., description="The original persona that was run.")
    transcript: str = Field(..., description="The full transcript of the conversation.")
    messages: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Normalized message trajectory including user, assistant, and tool turns.",
    )
    tool_calls: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Tool calls observed during the run, when wrappers expose them.",
    )
    artifacts: List[SimulationArtifact] = Field(
        default_factory=list,
        description="Multimodal artifacts observed during the run, such as audio, images, screenshots, files, and traces.",
    )
    events: List[SimulationEvent] = Field(
        default_factory=list,
        description="Normalized simulation events, including tools, memory, voice, browser/CUA, and framework spans.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Engine, scenario, timing, stop reason, and other run metadata.",
    )
    evaluation: dict | None = Field(
        default=None,
        description="Optional evaluation results (scores, reasons) keyed by template.",
    )
    audio_input_path: str | None = Field(
        default=None,
        description="Optional path to recorded customer (input) audio for this test.",
    )
    audio_output_path: str | None = Field(
        default=None,
        description="Optional path to recorded agent (output) audio for this test.",
    )
    audio_combined_path: str | None = Field(
        default=None,
        description="Optional path to a single WAV containing the mixed conversation.",
    )

class TestReport(BaseModel):
    """
    A comprehensive report aggregating the results of all test cases in a scenario.
    """
    results: List[TestCaseResult] = Field(default_factory=list, description="A list of results for each test case.")

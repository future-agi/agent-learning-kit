# Task Evaluation Synthesis Readiness

The V1 evaluation layer should not require every user to hand-write an
`agent_report` config before evaluating task evidence. Arbitrary saved task
evidence already contains task text, expected output, tool calls, framework
runtime state, world state, retrieval traces, and memory lineage. The SDK should
turn that evidence into a deterministic local evaluator config, then run the
same agent-report evaluator used by simulation artifacts.

Release-check runs `examples/sdk_task_evaluation_synthesis.py`. The example
does not provide a manual config. It passes raw field-service task evidence to
`evals.synthesize_task_evaluation_config()` and
`evals.evaluate_task_evidence_auto()`. The synthesized config must infer:

- task description and expected result;
- success criteria from expected output and task state;
- required/available tools from observed tool calls;
- forbidden patterns from negative state flags such as canary or credential
  leakage;
- source-grounding requirements from retrieval evidence;
- metric weights for task completion, tool selection, tool arguments,
  framework runtime, world contract, retrieval attribution, memory lineage,
  memory integrity, source grounding, and secret leakage.

The implementation stays local-first. It does not call a hosted judge or model.
External task-specific judges still belong behind the evaluation-hook probe;
this synthesis gate proves the base SDK can evaluate a new task artifact with
no bespoke evaluator code.

Sources used for the gate:

- https://arxiv.org/abs/2303.16634
- https://arxiv.org/abs/2410.10934
- https://arxiv.org/abs/2602.08672
- https://arxiv.org/abs/2605.30568
- https://platform.openai.com/docs/guides/evals

# fi-evals Cookbooks

Each cookbook solves a **real problem** you'll face when building AI applications.

| # | Cookbook | Problem It Solves | API Keys? |
|---|---------|-------------------|-----------|
| 01 | [Catch a Hallucinating Medical Chatbot](01_local_metrics.py) | Your chatbot makes up dosages and contradicts source material | No |
| 02 | [When Heuristics Aren't Enough: LLM-as-Judge](02_llm_as_judge.py) | Local metrics miss paraphrases — use Gemini to judge accuracy | Yes (`GOOGLE_API_KEY`) |
| 03 | [Is Your RAG Pipeline Lying to Users?](03_rag_evaluation.py) | Figure out WHERE your RAG fails: retrieval or generation? | No (optional for augmented) |
| 04 | [Protect Your LLM from Prompt Injection](04_guardrails.py) | Block jailbreaks, SQL injection, PII leaks, secret exposure | No |
| 05 | [Stop Toxic Output Mid-Stream](05_streaming.py) | Cut off LLM output the instant it turns toxic or off-topic | No |
| 06 | [Auto-Configure Your Testing Pipeline](06_autoeval.py) | "What should we test?" — describe your app, get a pipeline | No |
| 07 | [See Every LLM Call in Your Observability Stack](07_otel_tracing.py) | Trace calls with quality scores in Jaeger/Datadog/Grafana | No |
| 08 | [Teach Your Judge from Past Mistakes](feedback_loop_demo.py) | LLM judge keeps getting the same cases wrong — fix it with feedback | Yes (`GOOGLE_API_KEY`) |
| 09 | [Judge Images and Audio with Your LLM](09_multimodal_judge.py) | Verify AI image descriptions match the actual photo | Yes (`GOOGLE_API_KEY`) |
| 10 | [Evaluate Agent Simulation Reports](10_agent_report_evaluation.py) | Score simulate-sdk-style traces, tools, memory, autonomy-loop coverage, framework trace coverage, retrieval/memory attribution, multi-agent trace coverage, artifacts, browser/CUA trace coverage, voice trace coverage, environment injection, and pentest failures | No |
| 11 | [Evaluate Agent Trajectory Templates](11_trajectory_template_evaluation.py) | Score one reusable rubric for goal accuracy, ordered tool calls, Tool Call F1, policy adherence, browser action safety, memory correctness, and multimodal faithfulness | No |
| 12 | [Score Framework Transcript Quality](12_framework_transcript_quality.py) | Check LangGraph/LangChain event-stream methods, nodes, subgraphs, tool sequence, final state, output, and errors | No |
| 13 | [Catch Evidence Contradictions and Artifact Grounding Gaps](13_evidence_contradiction_artifact_grounding.py) | Detect answer claims contradicted by cited source text and verify artifact-backed claims against OCR/transcript/metadata evidence | No |
| 14 | [Score Multi-Agent Framework Transcripts](14_multi_agent_framework_transcript.py) | Check exported AutoGen/CrewAI/OpenAI Agents-style speakers, handoffs, tool owners, turns, and termination evidence | No |
| 15 | [Score Structured Artifact Semantics](15_structured_artifact_semantics.py) | Validate receipt/form/table/log fields, rows, event sequences, and answer claims against structured artifacts | No |
| 16 | [Score Cross-Trial Memory and Skill Quality](16_cross_trial_memory_skill.py) | Catch missing recall-after-write, forbidden memory keys, persistence gaps, and skill-step regressions across trials | No |
| 17 | [Score Decoded Voice Media Quality](17_voice_media_quality.py) | Check decoded WAV/PCM sample rate, duration, RMS/peak level, clipping, speakers, and trace coverage | No |
| 18 | [Score Semantic Browser Visual Diffs](18_browser_semantic_visual_diff.py) | Verify semantic changed regions while masking dynamic browser screenshot noise | No |
| 19 | [Score Browser Runtime State](19_browser_runtime_state.py) | Check cookies, localStorage/sessionStorage, page errors, and performance timing in browser traces | No |
| 20 | [Score Orchestration Graph Traces](20_orchestration_trace_quality.py) | Check workflow nodes, routes, retries, recovery, latency/cost budgets, terminal status, and graph state | No |
| 21 | [Score Streaming Trace Quality](21_streaming_trace_quality.py) | Check stream chunks, tool deltas, interruption recovery, drops, latency, gaps, usage, finalization, and state | No |
| 22 | [Score World Contract Quality](22_world_contract_quality.py) | Check actors, resources, transitions, invariants, policy gates, success conditions, and final state | No |
| 23 | [Score Adversarial Resilience](23_adversarial_resilience.py) | Check attack cases, untrusted surfaces, canaries, blocked tools, and safe responses | No |
| 24 | [Score Browser Mutation Resilience](24_browser_mutation_resilience.py) | Check stale selectors, storage drift, runtime faults, network latency, and fallback mitigation evidence | No |
| 25 | [Score Voice Timing Distributions](25_voice_timing_distribution.py) | Check VAD, end-of-utterance, STT, LLM, TTS, and full-turn p95 timing samples | No |
| 26 | [Score Export Auth and Pagination](26_export_auth_pagination.py) | Check framework and voice traces include authenticated paginated export evidence | No |
| 27 | [Score WebRTC Voice Stats](27_voice_webrtc_stats.py) | Check RTP, track, codec, audio-level, jitter, packet loss, and speaker evidence from voice traces | No |
| 28 | [Score MCP Tool Session Traces](28_mcp_tool_session_trace.py) | Check MCP tools/list schemas, tool calls, structured results, and errors from framework traces | No |

## Quick Start

```bash
cd python

# Run any cookbook (no API keys needed for 01, 03-07)
uv run python -m examples.01_local_metrics

# For cookbooks that need an LLM (02, 08)
export GOOGLE_API_KEY=your-key
uv run python -m examples.02_llm_as_judge
```

## What You'll Learn

- **Cookbook 01**: Build a validation layer that catches hallucinations, wrong dosages, and contradictions — all locally in <1 second
- **Cookbook 02**: When local heuristics fail on paraphrases, use an LLM judge with `augment=True` for production-grade accuracy
- **Cookbook 03**: Diagnose RAG failures by measuring retrieval quality (recall, precision) separately from generation quality (faithfulness, groundedness)
- **Cookbook 04**: Build a <10ms security middleware that blocks jailbreaks, code injection, PII exposure, and secret leaks
- **Cookbook 05**: Monitor streaming LLM output token-by-token and kill the stream when safety thresholds are breached
- **Cookbook 06**: Auto-generate test pipelines from app descriptions, customize thresholds, export YAML for CI/CD
- **Cookbook 07**: Wire quality scores into your OTEL traces so you can search for bad responses in Jaeger/Datadog
- **Cookbook 08**: Store developer corrections in ChromaDB, retrieve them as few-shot examples, and teach your LLM judge to not repeat mistakes
- **Cookbook 09**: Pass images and audio URLs to the LLM judge — evaluate image descriptions, UI screenshots, transcriptions with Gemini vision
- **Cookbook 10**: Evaluate full agent simulation reports locally: trajectory score, tool use, prompt-injection resistance, environment-injection resistance, memory integrity, autonomy-loop coverage, framework trace coverage, retrieval/memory attribution, multi-agent trace coverage, artifact coverage, browser/CUA action safety, browser trace coverage, voice turn-taking, voice trace coverage, and expected state
- **Cookbook 11**: Define one trajectory template and score agent goal accuracy, ordered tool calls, Tool Call F1, policy adherence, browser action safety, memory correctness, and multimodal artifact support without API keys
- **Cookbook 12**: Score LangGraph/LangChain event-stream transcript quality from plain dictionaries: required methods, nodes, subgraphs, tool sequence, final state, final output, and framework errors
- **Cookbook 13**: Catch source-supported contradictions and artifact grounding failures from local report dictionaries before reaching for an LLM judge
- **Cookbook 14**: Score multi-agent framework transcript quality from exported speaker, handoff, tool-owner, and termination records without installing the source framework
- **Cookbook 15**: Score domain-specific structured artifact semantics such as receipt totals, line-item rows, event sequences, and answer claims without a model judge
- **Cookbook 16**: Score cross-trial memory precision/recall, recall-after-write, persistence, and reusable skill regressions from local framework trace dictionaries
- **Cookbook 17**: Score decoded voice media quality from normalized WAV/PCM evidence without a model judge or media server
- **Cookbook 18**: Score semantic browser visual diffs, masked dynamic regions, allowed changed regions, and forbidden changed regions from local trace dictionaries
- **Cookbook 19**: Score browser storage-state capture, runtime/page-error events, and performance timing thresholds from local trace dictionaries
- **Cookbook 20**: Score framework-neutral orchestration graph traces for required nodes/routes, retry and recovery behavior, latency/cost budgets, terminal status, and expected state
- **Cookbook 21**: Score framework-neutral streaming/session traces for chunks, tool deltas, interruption recovery, drops, first-token latency, inter-chunk gaps, finalization, and expected state
- **Cookbook 22**: Score framework-neutral world contracts for actors, resources, required transitions, invariants, policy gates, adversarial surfaces, success conditions, terminal status, and expected state
- **Cookbook 23**: Score structured adversarial attack packs for required attacks, untrusted surfaces, canary leakage, blocked tool calls, and safe-response evidence
- **Cookbook 24**: Score browser mutation packs for stale selectors, storage drift, runtime faults, network latency, actionability evidence, and fallback selector mitigation
- **Cookbook 25**: Score voice timing distributions for VAD, end-of-utterance, STT, LLM, TTS, and full-turn p95 samples
- **Cookbook 26**: Score authenticated and paginated framework or voice export evidence from local report dictionaries
- **Cookbook 27**: Score WebRTC getStats-style voice evidence for RTP, track, codec, audio-level, jitter, and packet-loss thresholds
- **Cookbook 28**: Score MCP tool-session trace evidence for tools/list schemas, tool calls, structured results, and expected outcomes

# Changelog

All notable release changes for Agent Learning Kit are tracked here.

## Unreleased

Post-rc.1 increments on the release branch. Each one adds gates on top of the
66 proved at rc.1; the rc.1 entry below is historical and unchanged.

- Actors: one list, `actors.json`, of everyone the agent deals with
  who is not the agent: the caller, someone with the caller, a manager, a coworker, a system. Every actor has
  the same record (parent, goal, constraints, behaviour, knowledge, permitted actions, activation), and one
  with a `channel` also speaks in the conversation: beside the person (`audio_mix`), taking the phone and
  handing it back (`audio_handoff`), the person's own phone menu (`ivr`), or writing in a chat
  (`chat_message`). A scenario includes actors in its `actors`, each with what it says and when (its
  trigger) and the world actions it takes. An actor record with the id `caller` shapes the simulated person
  under every scenario's own persona.
- Each actor plays from one brief (`actor_brief`), built from the sealed `brief_inputs`: its own fields, the
  scenario's part for it, and the person's situation up to the person's details. Evaluator-only fields and
  files never reach it. The platform shows the same brief as what the actor sees.
- Spoken lines come in a voice of their own, a background line set as a ratio of the person's measured level
  with a limiter; per-actor and call-wide limits keep the person in charge. Every call records whether each
  actor was triggered, delivered or dropped, and why, on voice and chat alike.
- A build gives a speaking actor to between 15% and 25% of its scenarios by default; `aim_for` takes the
  share the person asks for (`call_actor_share`, 0 for none) in its place.
- A sub-goal may name the actor it is about (`actor`): it reads not applicable on a call that actor never
  came into and inconclusive when the actor came in and never reached the agent. A call one of its speaking
  actors never reached, with no sub-goal about that actor, is errored as inconclusive (`actor_missing`,
  naming the actor and why) instead of passing. The judge reads an actor's turns as someone other than the
  customer. Sub-goal results carry their `verdict` (pass, fail, not applicable, inconclusive) alongside
  `held`.
- Actors act in the world only through its own actions: the tool proxy lets an actor take only its
  `permitted_actions`, under a per-trial token that is never widened (without a private token the actors are
  left unplayed), and logs every attempt with whose it was and what came of it (committed, refused, faulted,
  duplicate). An actor's actions are sealed with the call's transcript under `world_actors`, never in the
  agent's tool trace. A bundle with actors also seals `actor_rules.json`, the rules its release checks actors
  by; a world without actors is seeded and sealed exactly as before.
- One door into the world (`world_door.WorldDoor`) for every surface: the tool proxy, the chat runner's
  in-process handlers and the actors' dispatcher all build one call and get one outcome; it never raises into its
  caller, and the agent's own calls behave as before. Chat agents now have actors acting in the world, a full
  action log and World changes. One conformance suite runs the same cases over every surface.
- An actor comes in one of five ways, with one meaning on every runtime: present from the start, on an event, when
  the agent contacts it, at a set time, or once a record exists (`conditional`). A kind its runtime cannot evaluate,
  or two actors that bring each other in, is refused when the scenario is written. Timeout and fallback (`silent`,
  `decline`, one of its own actions, or another actor) apply on calls, in chats and in the world.
- Live actors act on whatever they can see, with no per-actor cap; every limit is a setting on the environment
  (`limits` in `actors.json`, per scenario too), each actor may name its `model`, and actors' model calls count as
  simulator usage. A speaking actor decides its line and its world action in one turn.
- Conversations before the run (`pre_run`) are sessions in any place of the world, written by the actors in them
  when the scenario is kept, written again before sealing when an actor's record changes, and replayed into the
  world before every trial; `per_trial` writes a fresh one inside each trial. Typed `history` reads as one session.
- Every actor that acts through the world is in the call's actor summary; one that cannot act is recorded as the
  scenario's problem with the reason, and the judge is given the actors' actions and briefs. World changes add
  approvals, a "no change" line, and a judged sub-goal whose evidence never arrived reads `unmeasured`.
- The builder chat of a built environment can change its actors (`add_actor`, `drop_actor`) and nothing else
  of the world; adding or dropping one, or changing which scenarios include one, waits for the person's yes,
  and an automatic repair cannot change a scenario's actors.
- Multi-agent compatibility references preserve native Google ADK, LangGraph,
  and CrewAI orchestration, while Python command agents capture bounded tool
  evidence with explicit worker and task attribution.
- Multi-agent apps: the contract records the workers an app declares, and every Python agent's
  tool calls are read from its framework spans with the worker that made each, every hand-off and
  when that worker returned; a worker's run is never graded as a tool call. Each sealed worker event
  names its span, the parent hand-off it ran in, its kind (`delegated`, `transferred` or
  `tool_call`), whether that place was observed or is unknown, and whether its input or output was
  cut; a blank or repeated worker id is a contract problem.
- Builder chat: `edit_scenario` changes a kept scenario by its key with only the fields that change
  (`clear` resets a field), keeping its key, number, place and history; a rename alone is not
  proved again. A new scenario never takes a key the environment has used, a dropped scenario's
  included, and a repeated saved key is repaired so each scenario keeps its own.
- A chat conversation never sends the scenario's instruction as the simulated person's first
  message: an empty or echoed opening is asked for again, then for a greeting in their language,
  and the person's own session records what the agent actually heard.
- Runtime validation checks scenarios on one database lane per CPU by default, capped by the
  sandbox's cgroup CPU quota, instead of a fixed 16; `ALK_VALIDATION_DB_LANES` still overrides it.
- Docs corpus: the full `docs/` tree (quickstarts, per-track guides, framework
  pages, reference material, and the `docs/llms.txt` machine index) with
  machine-checkable page metadata, enforced by the new `docs_executability`
  release gate (67 gates).
- Live lanes: opt-in live execution lanes (LiveKit, Pipecat, LangGraph, MCP,
  A2A) behind per-framework extras, with the engine/public boundary enforced
  by the new `live_lane_boundary` release gate (68 gates).
- Optimizer expansion: optimizer portfolio routing, frozen capability
  profiles, and apply plans, enforced by the two new Phase-4 gates
  `optimizer_profile_matrix_readiness` and `capability_profile_freeze_readiness`
  (70 gates).
- Hosted Vapi and Retell calls now carry the target agent's provider call ID,
  native end reason, tokens, cost, and latency on result receipts instead of
  dropping that evidence; simulator usage remains separate.
- Hosted voice workers preserve stdout/stderr in the parent diagnostics stream,
  with per-call correlation, turn-state transitions, transcription dispatch,
  and simulator generation lifecycle events.
- Voice reports now seal transcripts after bounded STT/native-stream finalization,
  retain conversation evidence on failures, and explicitly report incomplete
  capture instead of silently completing with a partial transcript.
- Retell and LiveKit audio bridges close owned streams and sources, reap media
  tasks on cancellation, and drain queued playback before closing a provider call.
- Voice diagnostics include sanitized SIP error codes, transcript-finalization
  and cleanup results, isolated-worker exit status, and native crash backtraces.
- Outbound SIP calls no longer block on `wait_until_answered`. LiveKit v1.9.11
  retried that request with 3s/5s/7s deadlines, cancelling and redialing a
  ringing call so people and slow lines could never answer; the engine now
  waits for `sip.callStatus == "active"` in the room within the answer
  deadline and reports a rejected call's reason (e.g. `user_unavailable`).
- With `AGENTCC_API_KEY` (and `AGENTCC_BASE_URL`) set, the simulated caller's
  LLM and the `vertex-gemini` harness backend reach Gemini through Agent
  Command Center in the Gemini API's own format, as the claude backend already
  did, so no Vertex credential is needed. `fi.agentcc` is the one place that
  reads the gateway settings. A caller on `SIMULATOR_LLM_PROVIDER=vertex`, and
  the `vertex-gemini` backend, stay on Vertex: a bare model name such as
  `gemini-3.7-flash` is sent as `vertex_ai/gemini-3.7-flash`, which the gateway
  routes to Vertex rather than to the Gemini API.
- Simulator model fallbacks: `SIMULATOR_LLM_FALLBACK_MODELS` (ordered,
  comma-separated) gives the simulated caller models to switch to when an
  attempt fails or times out before it speaks.
- The simulated caller's speech goes through Agent Command Center too: with
  `AGENTCC_API_KEY` set, Deepgram and Cartesia speech-to-text and
  text-to-speech connect to the gateway's copy of each vendor's streaming API
  with the gateway key, so a sandbox needs no `DEEPGRAM_API_KEY` or
  `CARTESIA_API_KEY`. Without a speech provider named, the caller then speaks
  through Cartesia, as it does when a Cartesia key is present.
- The `vertex-gemini` backend no longer fails on its first reply with the locked
  ADK (2.7.1), which does not re-export `GetSessionConfig` from
  `google.adk.sessions`.
- Hosted chat transcripts stamp each turn with when it was delivered (`at`,
  on the tool trace's clock), so tool calls sort between the customer's turn
  and the agent's reply instead of after the conversation.
- Hosted voice calls whose agent writes its own tool trace no longer report
  each call twice: the agent's line (real arguments, structured result) takes
  the time from the LiveKit hook's line for the same call.

## v1.0.0-rc.1 — 2026-06-10

First locally-cut v1 release candidate. Package labels: Python
`agent-learning-kit==0.1.0`, TypeScript `@future-agi/agent-learning-kit==0.2.0`
(decision records D1/D2; the tag, not the semver, names the product milestone).

- One SDK and CLI (`fi.alk` / `agent-learn`) consolidating the
  `simulate`, `evals`, and `opt` engines — three engines, four workflows
  (test, simulate, red-team, optimize).
- 66 executable release gates behind `agent-learn release-check`, proved by
  `agent-learn release-proof` (`agent-learning.release-proof.v1`) on the cut
  commit.
- Distribution hygiene: the sdist now ships only `src/`, `tests/`, `examples/`,
  `docs/`, and the standard release files — `internal-docs/`, `uv.lock`, the
  roadmap, internal guides, the `typescript/` workspace, and build artifacts no
  longer leak; enforced by the new `package_distribution_hygiene` gate.
- `Development Status` classifier moved to `4 - Beta` (D3); `uv.lock` tracked
  in git and excluded from the sdist (D4).
- README claims reconciled with executable proof (LlamaIndex listed,
  `AGENT_LEARNING_API_KEY` named, OpenEnv positioning deduplicated with the
  robustness bar defined, install framing honest pre-publish, probe-promoted
  vs runtime-simulated coverage distinguished).
- Prepared v1 release-candidate documentation, Apache-2.0 licensing artifacts,
  community files, and release-proof handoff notes.
- Added a developer-first README opening with install, quickstart, workflow,
  release proof, repository map, and community links.
- Hardened `agent-learn release-proof` timeout handling and raised the default
  per-command timeout to 7200s for the expanded v1 proof suite (the full pytest
  suite, which executes every release gate inside the milestone test, exceeds
  the previous 2400s budget).

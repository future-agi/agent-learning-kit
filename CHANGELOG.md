# Changelog

All notable release changes for Agent Learning Kit are tracked here.

## Unreleased

- A voice agent's built-in suite evals now send `agent_prompt` only when the contract save copied the
  agent's instructions whole (the new `system_prompt_copied`): the lines the reading named in the
  source, as written, or a provider agent's whole prompt from its own definition. The provider's
  prompt is taken for phone and LiveKit connections (the prompt the user gave), for Retell with one
  general prompt and no states, and for Vapi (every system message of the assistant's model, in
  order). Where a provider definition is in force, repository lines are not used for the prompt.
  Not marked: a Retell agent with states or a conversation flow; a fetched Retell or Vapi prompt
  the profile's sanitizer changed (it rewrites a text that begins with a web address, and may cut
  it); and a fetched profile with no record of that check, such as one cached before fetches made
  the record. Without instructions copied whole, the eval that needs them is reported as not
  measured, with the cause `no_whole_instructions` or `no_instructions`, instead of being graded on
  a quote that may leave text out. Separately, a conversational reading with no whole prompt to
  copy that records neither a prompt nor rules, under any of their accepted names, is reminded once
  on its first save; a quote
  it then gives is kept as the excerpt, but it is not a whole copy, so it does not make the eval
  measured. A not-measured eval
  is neither a pass nor a fail: it is no checkpoint, it is listed with its cause in the summary and
  the JSON output, and it reaches the platform in the call's `harness_unmeasured`, never among
  `harness_evaluations`. An eval that needs any other input it can't be given is unchanged: skipped
  on a local run, and a grading error on a hosted one. Contracts saved before this change, and those
  from GitHub or spec sources, have no copied instructions, so their task-completion eval now reads
  not measured (TH-8376).

- Under per-user isolation, the harness's own world tables (`_alk_trial_faults`, `_alk_action_keys`, and the
  action log's `method` and `fault` columns) are made on the baseline before the world role's grant. Before,
  a proxied trial's every write was refused as `faults_unknown`, and every keyed actor action as `key_unknown`,
  because the world role could neither read a table made after its grant nor create one.

- A start-up timeout of a process the agent depends on, not the agent itself, now records the
  closed cause `dependency_not_ready_in_time` after exit 78, instead of no cause. Before, the
  platform could not tell it from the agent's own slow start. The process's name stays out of
  the cause, since the user's manifest names it.

- In a hosted simulation, traceAI's exporters again send traces to the simulation collector
  with the simulation's ingest key, including with `fi-instrumentation-otel` 1.5.1 and later.
  The cap below 1.5.1 is removed.

- `StepEfficiency` now measures repeated tool calls against total tool calls,
  rather than total steps, so several repeats within one step cannot produce a
  negative score with the default weights. Scores can shift for trajectories
  containing zero-call or multi-call steps when calls repeat; trajectories with
  one call per step and trajectories with no tool calls are unchanged. The
  corrected component also changes `TrajectoryScore` (30% efficiency by default).
  The existing `redundant_steps` details key is preserved and still counts
  redundant calls.

Post-rc.1 increments on the release branch. Each one adds gates on top of the
66 proved at rc.1; the rc.1 entry below is historical and unchanged.

- Adaptation on the Wave A verticals (TH-7951, TH-8751): a customer's agent that is an MCP client is
  **declared** (name, instructions, tool schemas; connector `mcp_client`, `adaptation/declared.py`)
  instead of inspected at a provider, and adapted exactly as a Vapi or Retell agent is: every
  declared tool is routed, the mode is chosen on the schemas, the builder maps and proves the
  binding, and the declaration's digest is the agent's identity, so a run with another declaration
  is refused. The harness now **derives trajectories** from the reference solutions wherever the
  binding inverts them (`binding_trajectories.py`: one step per agent tool, arguments renamed,
  converted, mapped or aliased); `submit_binding` takes a draft without trajectories and says which
  scenarios still need one, with the reason, so a hundred-scenario suite is proved without a model
  writing a hundred trajectories. The world's MCP endpoint (`world_mcp`) loads the bundle's
  binding: under a `tool_routed` binding `tools/list` is the agent's own tools and every
  `tools/call` is routed through the binding, so a customer's agent, or its Gmail, Drive or Slack
  MCP tools under their official names, reaches the world's records in its own vocabulary;
  `compose` composes `canonical` and `tool_routed` bindings on `mcp_client` over `world-mcp`.
  Proved on PostgreSQL: a renamed agent (no name in common with the environment) on every scenario
  of all nine verticals, and Banking's hand-written customer CardGuard (own names for the bank's
  tools, the official Gmail and Drive tools mapped onto themselves, one unsupported tool, two
  instructions against the agent rules), with every trajectory derived
  (`tests/harness/test_adaptation_verticals.py`, `tests/harness/adaptation_fixtures.py`).
- The local graders (`alk run`, `run/live.py`, `run/grade.py`) judge the judged half of a `both`
  goal as well as running its check, as the hosted scheduler already did: a goal with both halves
  holds only when both do, so a draft that falsely claims a refund no longer passes on its code
  half alone.
- Wave A increments 7 to 10 (TH-8750): four new vertical packages, each with 100 scenarios over
  linked business and MCP records: `clinic_scheduling` (V02; Calendar as the availability source,
  Gmail), `service_scheduling` (V12; Calendar across two time zones and a clock change, Gmail),
  `sales_intake` (V17; Gmail, Calendar and Slack in one world) and `internal_knowledge` (V11; the
  Drive corpus is the business system, Slack decisions). Each carries its five workbook cases,
  hand-written seeds and builder-generated scenarios proved on its own world, a validation test
  with a wrong-run matrix, and a README rendered from the suite and the test; each seals on SQLite
  and twice on PostgreSQL with identical digests and was run live (Claude Code over MCP, Gemini
  conversations, an E2B build).
- Wave A increments 3 to 6 (TH-8750): `retail` (Gmail and Drive), `hr` (Slack and Drive),
  `internal_it` (Slack and Drive) and `saas_subscriptions` (Gmail and Slack) each reach 100
  scenarios over linked business and MCP records, with new business tools, world checks for the
  linked data, evidence-aware checks, a validation test with a wrong-run matrix per vertical, and
  READMEs rendered from the suite and the test. The new scenarios were generated by the
  environment builder from requirement files in `internal-docs`, then proved on each package's
  own world; each package seals on SQLite and twice on PostgreSQL with identical digests, and was
  run live with Claude Code over MCP, Gemini conversations and an E2B build.
- A native agent whose tools are a separate HTTP server in its source records it as `runtime.tool_service`;
  the bundle starts that server behind the tool proxy, so its tools run and are recorded like a Compose
  `tools-api`.
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
  share the person asks for (`actor_share` with `min` and `max`: every scenario, none, or a range) in its
  place, and the actor list keeps it for later sessions.
- A sub-goal may name the actor it is about (`actor`): it reads not applicable on a call that actor never
  came into and inconclusive when the actor came in and never reached the agent. A call one of its speaking
  actors never reached, with no sub-goal about that actor, is errored as inconclusive (`actor_missing`,
  naming the actor and why) instead of passing. The judge reads an actor's turns as someone other than the
  customer. Sub-goal results carry their `verdict` (pass, fail, not applicable, inconclusive) alongside
  `held`.
- Actors act in the world only through its own actions: the tool proxy lets an actor take only its
  `permitted_actions`, and logs every attempt with whose it was and what came of it (committed, refused,
  faulted, duplicate). The actors' files are locked to the harness; isolation from the agent in production
  comes with process isolation, in a later release, and until then an agent running as the same user can still write the voice actions file
  under an actor's id. An actor's actions are sealed with the call's transcript under `world_actors`, never in the
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
- The action log records each call's world action in full (`action`, beside `name`), and the door numbers a fault's
  calls, holds its lock, answers an actor's repeated key and says a fault did not take effect by that action, so
  `ticket.update` is never taken for `crm.update`; a key reused for another action is an `idempotency_conflict`. An
  `on_tool` trigger waits for the action in full. An actor waiting for a record only it may write is refused when the
  scenario is written. A timeout another actor answers reads `answered_by` only once that actor has acted or spoken,
  else `limit_blocked` or `unresolved`.
- Each trial's end-of-run world state is kept: its compared tables and its action log, uploaded as a `world_state`
  artifact (`futureagi.world-state.v1`), so a fixed check can grade the trial again without calling the agent.
- World changes on every trial with a door, actors or not: every new world is seeded with the full action log, and
  a trial whose tools run where no door stands seals `{"recorded": false, "why": "in_process_tools"}`. The runner
  snapshots the world's tables at a trial's start and end (read-only, by primary key, `_alk_*` never compared,
  tables over 5,000 rows or without a key named in `skipped`, at most 100 changes listed) and seals `records`
  and `start` (`seeded`, `differs` with the tables, or `unknown`). `start` is read after the trial's reset and
  before its pre-run sessions write; `records` compares the end against the world after them, so pre-run writes
  are neither a start that differs nor the trial's changes. A check that reads an unreadable action log
  (`EvidenceUnavailable`) is unmeasured, never settled on an empty log; the scenario errors as
  `evidence_unmeasured`.
- Two more fault modes: `unavailable` (the world never sees the call; the agent hears 503 at once) and `error`
  (the scenario's `status` 400-599 and `message`). The door answers the agent's keyed retry (`Idempotency-Key`)
  from its first result, as it does an actor's; a fault before commit releases the key. A write the world commits
  twice after a lost answer fails the scenario as `duplicate_commit`, sealed in `world_actors.duplicates`.
- A Python agent's in-process tools (`import`, `construct`) get a world door of their own: a `world-door` process on
  the world's database, and a `sitecustomize` hook that compiles the door in as each tool's innermost decorator.
  Faults, the action log and actors' record changes then reach them; an actor's action on an in-process tool is
  refused with why.
- The builder records policy points it could not model (`note_unresolved`), kept in `actors.json` as `unresolved`
  for the person to decide above the cast.
- The door logs each call as a read or a write (`effect`): a tool entry's declared `effect` (`read` or `write`), else
  a GET, HEAD or OPTIONS call reads and anything else writes, as before. A trial's state changes count reads apart
  (`read`, `read_actions`), never as committed, and an approval's writes around the decision leave reads out.
- The builder chat of a built environment can change its actors (`add_actor`, `drop_actor`) and nothing else
  of the world; adding or dropping one, or changing which scenarios include one, waits for the person's yes,
  and an automatic repair cannot change a scenario's actors.
- Builder chat: every turn's ending reports what that turn spent: tokens, USD, unpriced
  turns and only the models it called. The ending is kept before the turn's final
  checkpoint, so a restart after a failed delivery replays the same ending, usage included.
  A chat sandbox of its own stores each ending once in its coordinator state: a checkpoint
  adds only the endings new or changed since the last one (the turn journal is still
  stored whole).
- A world whose JSON columns hold values the seed import refuses, such as a jsonb string
  written as bare text, is refused where it is written: `save_world` and the builder's
  `change_world` name the table and column and the JSON text expected, instead of the
  environment failing its seed import at validation. The columns are those the agent's
  own `CREATE TABLE` statements declare `json` or `jsonb`.
- A claim for a scenario's coverage overlay (a refusal, an escalation, an out-of-scope request)
  that its catalogue row settles by what was said (`verifier: llm`, no check, reference or
  observed record) no longer requires tool evidence, so a correct agent that makes no tool call
  is graded instead of ending `evidence_missing`. The claim is matched by the catalogue's
  `overlay` field or by holding the overlay's whole name. Every other sub-goal, coded ones
  included, still requires evidence.
- A call whose declared tool capture could not be read ends `evidence_missing` even when its
  scenario needs no tool call, instead of being graded as a call that made none. Unread means an
  unreadable trace file, a trace file with no readable record, or a `tool_trace` world database
  that is missing or fails. A hand-off is never graded as a tool call. A traced declared tool's
  oversized record is now cut by value, so its trace line stays valid JSON. Only an oversize
  value is cut, and it says it was cut; the other values keep their types.
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
- Chat readiness tool routing: the tool proxy logs each call's full path, so two service tools
  whose paths share a last segment are told apart and an older record naming only that segment
  credits neither; a tool request's handler is bounded by the request deadline and a handler that
  outlasts it leaves the tool unobserved; an earlier answer is reused only for the same candidate
  and tool definition, within the one request and time budget for the check.
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
- Hosted chat simulations are metered again. Since chat calls moved into an
  isolated worker, the worker had no usage reporter, so no chat call was checked
  against the usage limit or recorded as `text_call`. The parent now checks
  before starting the worker and records the simulator tokens the worker
  measured, for repository and Retell chat agents alike.
- Environment releases can attach MCP servers (`servers.json`): each world
  starts them fresh, their tools reach agents as `<id>__<tool>`, and their
  calls are traced like the release's own tools. The first is ALK's read-only
  `sql` server, which reads only the tables a release lists through a reader
  role. Scenarios that use attached tools are proved on PostgreSQL. The clinic
  release attaches it and adds a schedule question only it can answer; for
  agents without that tool, adaptation reports the scenario unscorable.
- The official filesystem MCP server (`@modelcontextprotocol/server-filesystem`
  2026.1.14, MIT) can be attached with a `directory` under the release's
  `attached/`; it is installed in the hosted image from a committed lockfile and
  never downloaded at run time. The clinic attaches its patient guides through
  it (read tools only) and adds a first-visit question only the guide answers.
- Our own agent can run on an environment release: connector `hosted_agent`,
  an `AgentSpec` (instructions and a model) whose digest the binding pins. Its
  bundle serves the world's tools over MCP (`fi.alk.harness.world_mcp`, `/mcp`,
  the release's tools plus attached servers'), and the agent runs in the chat
  worker as a tool loop through Agent Command Center, billed in `text_call`.
  Its evidence is the world's trace, not anything it reports.
- A scenario with no tool calls is graded normally when the harness wrote and
  read the trace itself (our agent, or a request-bound provider run); only an
  unreadable trace is still `evidence_missing`.
- Suite sealing refuses a simulator prompt without `{{ persona }}` and
  `{{ instruction }}` slots. The clinic's had neither, so in chat runs the
  simulated patient never saw the scenario's task; it now has both.
- Environment sealing refuses handler files with no tool, handler modes other
  than `environment` (`customer_source`/`customer_spec` are reserved), attached
  tool names an agent cannot be offered, and an extension that changes its
  parent's `attached/` files. Resealing a sealed directory with a different
  parent or different scenario origins is refused instead of silently turning
  an extension into a root or a derived suite into a base one.
- The PostgreSQL proof also replays every reference call as crashed, as the
  SQLite proof does, so a check that credits an attempted call without its
  effect is refused on both engines. An attached tool counts as changing state
  unless its server declares it read-only; the `sql` server does.
- The clinic's world no longer declares foreign keys, CHECK constraints or a
  partial index, which PostgreSQL seeds dropped anyway; its handlers enforce
  those rules, so both engines grade alike. Its release digests changed.
- Hosted adaptation keeps the scratch PostgreSQL's superuser DSN in
  `.authoring-credentials/` beside the workspace, which the model's file tools
  refuse, instead of in `adaptation/inputs.json`.
- Suite proofs gain a fourth gate on both engines: the untouched world is graded
  against the solution's calls with every successful state-changing call still
  `ok` but without its result (`mutates_state` in `tools.json`). A check that
  holds credits the call rather than its effect, and the scenario is refused.
  The clinic's suite passes unchanged.
- Design workspace (`fi.alk.harness.design`), the first part of the environment
  builder: `open_design` unpacks a root template (environment and suite) into a
  flat workspace, `release_view` materializes the release roots through an
  allow-list, and `seal_design` derives the policy rows, re-saves the world as an
  environment's, refuses schema PostgreSQL seeds would drop or rewrite (foreign
  keys, CHECK, partial and plain indexes, views, triggers, generated columns,
  expression defaults, other types; a template's are listed as inherited), and
  refuses check files that differ from the catalogue, before sealing both
  releases on PostgreSQL.
- Design mode (`python -m fi.alk.harness.design run|hosted`): the builder
  interviews the person one question at a time, and nothing is built until they
  approve `propose_design`. It then builds the world with the world server and
  its own tools (`define_tool`, `change_tool`, `drop_tool`, `add_policy_rule`,
  `change_policy_rule`, `drop_policy_rule`, `detach_server`), writes the suite
  with the scenario tools, and seals with `finish_design`. Each handler is checked
  before it is kept: only allow-listed imports, no builtins that reach outside
  the world, a sample call whose effect matches `mutates_state`, missing required
  arguments refused, and every argument used. A designed suite must grade every
  agent rule and cover exactly the approved use cases. An unanswered question
  parks the design (hosted: the job ends `design_waiting` with the question
  checkpointed), and continuing asks it again and resumes.
- Designs attach MCP servers: `attach_server` validates a `sql` or `filesystem`
  server against the registry, the world's tables and the folder, starts it
  against the design's world and keeps only read-only tools; `write_attached_file`
  writes the documents a filesystem server reads. Every environment's attached
  tools must be read-only (each registered server lists its attachable tools).
  Attached tool names reach the scenario writers (the world summary lists them
  with what they take), the health check and the readiness gates, and a
  scenario whose solution calls one is proved on PostgreSQL when it is
  submitted, off the event loop, against a template database kept per design
  view (`design/proof.py`, sharing the seal's `postgres_verdict`).
- Design documents (`--documents`; hosted under `/work/documents`): each upload
  is parsed in a child process with an allow-listed environment and CPU, memory
  and output limits (PDF with pypdf, XLSX with openpyxl, DOCX with the standard
  library, CSV, TSV, JSON, JSONL, text; oversized zips refused), into text and
  tables the builder reads with `list_documents` and `read_document`.
  `import_rows` inserts a table into the world with each value coerced to its
  column's declared type, and `attach_document` writes a document as markdown
  under `attached/`. Both require a decision about real people's details in the
  document (`personal_details`: none, replaced with made-up ones, or kept at
  the person's word), and both are recorded with it and their source's sha256
  in the sealed environment's `metadata.provenance`. New `documents` extra,
  installed in the hosted image.
- The Gemini backend names its ADK agent with a valid identifier for any
  session id: a hosted chat whose conversation id began with a digit failed
  every turn with a ValidationError.
- Designed tools must be valid JSON Schema (`parameters` and `returns`), checked
  at `define_tool` and when a release loads: a sealed Gmail design had shipped
  `'str'` and `'int'` types that MCP clients reject. Tool names may contain `-`
  and run to 64 characters, as MCP and model function calling allow, and the
  release name and digest patterns are anchored. `ToolError` takes an optional
  stable `category` (`invalid_input`, `not_found`, `permission_denied`,
  `conflict`, `policy_blocked`, `rate_limited`, `unavailable`,
  `timeout_unknown_outcome`) that leads the message the agent reads; both
  engines raise the one class.
- A finished design refuses a second `finish_design` or `stop_design`: a builder
  that carried on after sealing resealed over the registered output and the job
  failed with `design_result_missing`.
- The hosted chat emits `question_answered` when the builder takes a reply, so
  the platform settles the question while the turn carries on rather than at
  `turn_completed`; needs the platform that accepts the event kind.
- Environment-authored handlers (designs and releases) run in an isolated child
  process when `ALK_HANDLER_ISOLATION` is set (`fi.alk.harness.world.handler_host`).
  The hosted image sets `namespace`: a user namespace as a uid from svc-control's
  subordinate range, an empty environment, no network, and empty mounts over
  `/work`, `/run`, `/home` and `/tmp`; the call fails rather than fall back when
  the namespace cannot be made. The handler reaches the world only through its
  `db`, whose operations the calling process answers; messages are JSON lines,
  values keep their types, refusals keep their category, and a hung handler times
  out. Run processes inherit the setting. A model-written handler could otherwise
  read the builder's model and platform credentials (or a run's secrets).
- Finishing and stopping a design are serialized: two concurrent `finish_design`
  calls no longer both seal into one output.
- Personal-details replacement in `import_rows` and `attach_document` matches
  numbers as written (a phone number in a numeric cell), and the tool names each
  requested replacement the document never held.
- XLSX sheets whose titles read the same as file names (`A B`, `A-B`) each get a
  part of their own; a part keeps its sheet's `title` as written.
- Ten prebuilt environments designed by the environment builder from the
  phase-1 sheet, committed as source the way the clinic is (tools, policy,
  handlers, world SQL, base-suite scenario folders):
  `lost_card_and_dispute_desk` (V01), `hr_leave_desk` (V13),
  `retail_returns_desk` (V15), `subscription_billing_desk` (V16),
  `it_access_desk` (V20), `slack_channel_digest` (M01), `gmail_inbox_triage`
  (M02), `calendar_scheduling_desk` (M03), `drive_policy_search` (M04) and
  `notion_action_tracker` (M05). Each builds with
  `python -m fi.alk.environments.<name>.build --out DIR`, through the shared
  `fi.alk.environments.designed`.
- Wave A regroup (TH-8750, stage 2): the environment is the vertical. `hr_leave_desk`,
  `retail_returns_desk`, `subscription_billing_desk` and `it_access_desk` become
  `hr`, `retail`, `saas_subscriptions` and `internal_it`, beside `banking`; the
  V16 acceptance cases carry their workbook IDs like V01, V13, V15 and V20; each
  vertical package holds a `README.md` (tools, scenarios, coverage map, known
  gaps) and `sample_agent.md`; `fi/alk/environments/README.md` is the inventory.
  `notion_action_tracker` (M05, outside Wave A) is removed. Banking's simulated
  customer is told they are contacting the bank's support, not the old package
  name. Content is otherwise unchanged; every package seals on PostgreSQL and
  rebuilds to the same digests.
- Wave A stage 1 (TH-8750): Banking's support mailbox, designed by the
  environment builder (Gemini 3.7 Flash on Vertex, 19 minutes, $2.51) from
  `internal-docs` `e2e-harness/live/design/banking/requirements-3.md` and
  exported to `fi/alk/environments/banking`. 8 threads and 13 messages from the
  bank's customers, linked to their records (a complaint thread ending in the
  customer's correction, the thread that opened dispute D01, which now lives
  in the world, a same-subject thread from another customer, an email with
  instructions aimed at the assistant, a restricted fraud-team thread),
  support labels, and `check_email_banking_consistency`, which fails the seal
  when an email names a customer, card, transaction or dispute that does not
  exist or does not match. 8 new scenarios for the two mailbox use cases
  (26 in all); the 18 existing ones are unchanged. The outcome checks
  `dispute_filed_and_reported`, `card_locked_upon_confirmation` and
  `draft_created_for_review` are reference-aware: a dispute for the
  transaction the customer first named, a lock of another card or a draft on
  another thread fails (`tests/harness/test_banking_mailbox.py`). Hand
  corrections on export: the builder's checks credited the call rather than
  the target, seven personas carried invented surnames, and the existing
  dispute lived only in one scenario's setup.
- Wave A stage 3 (TH-8750): the four MCP capabilities match the official
  servers. Each of `slack_channel_digest`, `gmail_inbox_triage`,
  `calendar_scheduling_desk` and `drive_policy_search` pins the official
  server's `tools/list` in `mcp_reference.json` (endpoint, sources, input
  schemas, result fields, tools not attached, local extensions), and its tools,
  handlers, reference solutions and checks now use the official tool names,
  argument names and result shapes: Gmail's `threadId`/`labelIds`/
  `replyToMessageId` with `unlabel_thread` as its own tool; Calendar's
  `startTime`/`endTime`/`attendeeEmails` and `Event` objects with
  `search_events`; Drive's `fileId` and query syntax with `File` objects;
  Slack's `slack_read_channel`, `slack_read_thread`,
  `slack_search_public_and_private`, `slack_search_channels` and
  `slack_send_message`, with the acting user held in the world
  (`current_actor`) as the OAuth user is on the official server. Approval,
  idempotency and version arguments stay as documented local extensions.
  Banking carries the realigned Gmail tool set.
  `tests/harness/test_mcp_capability_reference.py` holds each package to its
  reference. Review fixes: a start-only `update_event` keeps the start's UTC
  offset on the derived end time, attendee ids never reuse a surviving row's
  id, `list_drafts` applies `to:`/`cc:`/`bcc:`/`subject:` and refuses other
  operators, and the Slack timeout criteria accept the channel read-back route
  (`tests/harness/test_mcp_capability_handlers.py`).

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

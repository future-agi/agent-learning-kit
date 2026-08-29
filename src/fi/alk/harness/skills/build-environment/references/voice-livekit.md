---
name: voice-livekit
description: "The repository CONTAINS the voice agent process. Evidence: livekit-agents or livekit.plugins in pyproject/requirements, an entrypoint decorated with rtc_session or a WorkerOptions call, an AgentSession built from stt/llm/tts. You can run their worker yourself. NOT this file if the repository has no agent process and only serves webhooks (voice-hosted-platform.md), and not for a browser or retrieval agent that happens to speak."
---

# Voice agents that bring their own LiveKit worker

> **Selection check.** You are in the right file if you found an agent process in the repository that joins a LiveKit room. If the repository has no agent process at all and only exposes webhook endpoints, stop and read `voice-hosted-platform.md` instead.

You are making a stranger's voice agent testable without changing what it is. The agent already
works somewhere; your job is to stand up the world beneath it faithfully enough that a failure in
a run is the agent's failure and not yours. Everything below assumes you can already read Python,
Docker and HTTP. **ALK owns the call. These are the footguns.**

| Task | Approach |
|---|---|
| Place a call, drive turns, record | Never build this. `call_runner` + the livekit engine already do it |
| Build the world the agent's tools sit on | Its own Compose/migrations if it ships them; otherwise author a store |
| Make the caller speak | `simulator_voice.simulator_definition` + `caller_scenario` |
| Prove the world before grading | `probe.verify_runtime_tools`, then the QA section below |
| Read what a finished call produced | `scripts/check_call_evidence.py` |

## Scripts

Paths are relative to this skill's directory.

| Script | What it does |
|---|---|
| `scripts/probe_voice_providers.py` | Asks Cartesia/Deepgram a trivial question and prints the HTTP truth. **Run this before any hosted run.** A provisioning pass costs ~13 minutes before the first word, so a dead key is otherwise discovered at the worst moment and presents as an agent fault. `402` means out of credit |
| `scripts/check_call_evidence.py transcript.json [--receipt receipt.json]` | The QA gate below, as a command. Exits non-zero and names what is missing. Catches the mute-simulator case specifically, because that one reads as a stalled agent |

## What already exists - do not rewrite it

These modules are importable right now. Reimplementing any of them is a defect, not a choice:
what you write will diverge from what the platform actually runs, and the run will grade your copy.

```python
from fi.alk.harness.simulator_voice import (
    simulator_definition,   # (get, persona) -> SimulatorAgentDefinition: stt/tts/llm for the caller
    caller_scenario,        # keyword-only -> Scenario: the person, their situation, their number
    simulation_spec,        # keyword-only -> SimulationSpec: everything the engine needs
    voice_providers,        # (get) -> (stt, tts): cartesia when keyed, else deepgram
    fixture_caller_phone,   # (fixture) -> str: the number the target must see
)
from fi.alk.harness.world.probe import verify_runtime_tools   # (world, contract) -> RuntimeToolVerdict
```

`get` is a lookup you supply: `lambda name: config.get(name.lower()) or environ.get(name) or ""`.
That indirection is why one seam serves both the local and hosted lanes; do not replace it with
direct `os.environ` reads.

`fi/alk/harness/call_runner.py` places the call. `fi/simulate/simulation/engines/livekit.py` owns
room lifecycle, turn taking, silence backstops, `endCall`, transcripts and recordings.
`fi/simulate/simulation/livekit_models.py` resolves the caller's STT, TTS and LLM.

**Never invent an ALK signature.** If you need a shape not shown here, read the module. A guessed
keyword fails at call time, tens of minutes into a run, in a sandbox you cannot attach to.

This is the whole assembly, as `call_runner._build_spec` really does it:

```python
def setting(name: str) -> str:
    return str(simulator_config.get(name.lower()) or environ.get(name) or "")

simulator = simulator_definition(setting, doc.get("persona"))
spec = simulation_spec(
    run_id=run_id,
    room_name=room_name,
    agent_name=agent_name,
    system_prompt=doc["instruction"],
    livekit_url=livekit_url,
    recording_dir=recordings_root / run_id / "recordings",
    scenario=caller_scenario(
        name=str(doc.get("scenario_key") or "harness-voice"),
        persona=doc.get("persona"),
        situation=doc["instruction"],
        fixture=doc.get("fixture"),
        tts_provider=simulator.tts.provider,
    ),
    simulator=simulator,
    direction="agent_first",
    max_seconds=call_timeout_seconds,
    min_turn_messages=6,
    agent_first_silence_seconds=60.0,
    run_seconds=run_seconds,
)
```

## Footguns

Each of these cost a working day at least once. Every one presents as something other than what
it is, which is why they are listed rather than left to be rediscovered.

- **`GOOGLE_CLOUD_LOCATION` must be `global`.** Any `gemini-3.x` model **404s on a regional
  endpoint**. The agent then never speaks, the call ends at a fixed ~133s with `turns: 0` and a
  silent recording, and it looks exactly like a LiveKit fault. `CLOUD_ML_REGION` is a different
  variable and stays as it is.
- **Set `AGENT_LLM_MODEL` explicitly.** Left unset, the agent falls back to `gemini-2.5-flash-lite`,
  which emits `FinishReason.MALFORMED_FUNCTION_CALL` on its first tool call, retries, and never
  speaks. The transcript ends after the greeting.
- **A lite model cannot drive a tool-heavy agent.** Measured: every flash-lite variant returns
  `MALFORMED_FUNCTION_CALL` against a real 15-tool surface while answering a two-tool probe
  perfectly, so a synthetic check will not catch it. The **simulator** may use a lite model (it
  has one tool); the **agent** may not.
- **`gemini-3.7-flash` is the wrong model for real-time voice.** Measured time-to-first-token:
  ~3.3s against ~0.7-1.0s for `2.5-flash`, `2.5-flash-lite`, `3.5-flash` and `3.5-flash-lite`. At
  four serialised tool round-trips per turn that is roughly 11 extra seconds of silence per turn.
- **A dead TTS key reads as a stalled agent.** The simulator still writes its line into the
  transcript, so the text is all there; only `started_speaking_at`/`stopped_speaking_at` are
  `null`. The agent hears real silence and says nothing, and the run reports
  `conversation_silence_timeout`. Check the timestamps and run `scripts/probe_voice_providers.py`
  before touching the agent.
- **The hosted guest emits WARNING and above only.** `logger.info` is invisible in a hosted run.
  Any diagnostic you add for a hosted lane must be `logger.warning` or it will not exist when you
  need it.
- **Bump `ALK_HOSTED_SOURCE_REVISION` in `Dockerfile.hosted` whenever guest code changes.** The
  image is cached by that string. Without a bump your change **silently does not run** and you
  will debug the old code's behaviour.
- **One sandbox at a time.** Each takes 8 GiB against a 10 GiB account cap, so a leftover sandbox
  makes the next run fail with `sandbox_launch_failed` before the guest starts. Clear it first.
- **A simulator that never calls `endCall` can starve the world pool.** Two sides trading
  farewells ran 76 turns and 285 seconds, held its worker past the pool's patience, and cost the
  remaining scenarios their worlds (`world_pool_exhausted`). The engine now ends a farewell-only
  exchange, but a caller prompt that never concludes will still burn a run.
- **Numbers reach tools as digits, not as spoken words.** Seed and normalise phone numbers, dates
  and amounts the way the worker itself does. A store that accepts one formatting convention turns
  an ordinary speech-recognition variation into a false agent failure.
- **A sub-goal that needs the caller to accept an optional offer is not gradeable.** The agent
  offers a confirmation SMS, the persona declines, and a correct agent fails. Either write the
  willingness into the person or check that the offer was made, not what followed it.

## Building the world

Use the repository's own Dockerfile, Compose file, lockfile, migrations and seed process wherever
they exist. Point the worker at an isolated instance of the service it already ships, through its
documented injection seam only.

**Do not replace an unavailable tool with a stub, a canned response or a generated endpoint.**
That changes the subject under test from their agent to your mock, and every result afterwards is
about the harness. If the worker's state is process-local with no loader or injection seam, say so
and stop: a plausible second copy is worse than an honest report.

The baseline needs enough real records to exercise a successful lookup, an ordinary refusal, a
repeated action and a state transition. Per-caller edge states belong in scenario setup, not the
baseline.

## QA (required)

Do not report a world or a run as good until these pass.

**World QA.** `verify_runtime_tools(world, contract)` returns a `RuntimeToolVerdict`. Read
`verdict.ok`, never `not verdict.broken` - `checked=False` means nothing was proven, and treating
an empty list as success is the exact defect this gate replaced. A refusal is a working tool; only
a crash or a 5xx counts against it.

**Call QA.** `python3 scripts/check_call_evidence.py transcript.json --receipt receipt.json`.
It requires: both roles present, real `started_speaking_at` on the turns, at least one recording
artifact, and sub-goals that were actually judged.

**Evidence QA.** The platform renders exactly these, and shows zeros or blanks without them:
transcript with real speech timing, recording artifacts, tool trace, sub-goal verdicts, receipt.

## Avoid

- Writing a second call loop, STT/TTS wrapper, or copy of the agent's tools when ALK has one.
- Reading `os.environ` directly instead of the `get` lookup the seam expects.
- Declaring a runtime tool proven because the build stage recorded it. The build stage cannot
  reach runtime tools at all; they are `unproven` until something executes them.
- A build-time sequence that pretends to invoke an in-worker function.
- A caller prompt that names tools, narrates a test, repairs the agent's mistakes, or invents an
  account, booking or phone number it was never given.
- Blaming the agent for a silent call before checking the caller's speech timestamps.
# Voice agents that bring their own LiveKit worker

The agent joins a room and talks. Build what its real tools and dependencies sit on; do not build
the call, the worker, an STT/TTS replacement, or a second copy of its tools.

ALK already owns the call path. Reuse it rather than writing another one:

- `fi/alk/harness/call_runner.py` places the hosted call and records its outcome.
- `fi/alk/harness/simulator_voice.py` creates the caller persona, instruction, providers and phone
  identity. Both lanes use it.
- `fi/simulate/simulation/engines/livekit.py` owns room lifecycle, turn taking, silence limits,
  `endCall`, transcripts and recordings.
- `fi/simulate/simulation/livekit_models.py` resolves the caller's STT, TTS and LLM.

## First, identify the real boundary

Read the worker startup and every tool registration before changing configuration. Establish:

- Which environment variables select its datastore and service URLs.
- Which migrations, seeders and queues must exist before the worker starts.
- Which tool calls are internal worker functions versus calls to a separate shipped service.
- Which records identify a caller or a conversation, and how spoken forms are normalized.

Use only the documented injection seam. Point the worker to an isolated instance of the service or
store its repository already ships. If its state is process-local and has no loader or injection
seam, do not create a plausible second copy: report that the submitted worker cannot be tested
against an isolated world yet.

## Build the dependency environment

Use the repository's Dockerfile, Compose configuration, lockfile, migrations and seed process when
they exist. The world must contain enough real records to exercise successful lookup, ordinary
refusal, repeated action and state transition paths. Keep the agent's awkward source data intact;
scenario setup, not the baseline, creates per-caller edge states.

Check that the worker's configuration reaches the isolated dependency before declaring it ready.
Do not replace an unavailable tool with a webhook stub, canned response or a generated endpoint.
That changes the subject under test from the worker to the harness.

## Write voice-specific checks

World checks should inspect durable effects the real tool service leaves behind: a reservation,
case, payment intent, appointment, audit event or conversation state. They should also reject a
baseline that is already mid-call or contains the residue of a setup probe.

Runtime-only tools remain unproven during environment authoring. Do not describe an uncalled tool
as working, and do not add a build-time sequence that pretends to invoke an in-worker function.
The hosted runtime must later execute those tools through the worker's real evidence seam.

## Write the caller for speech, not text

The simulator prompt must make the caller speak one short turn at a time, provide identifiers only
when scenario setup gives them, answer direct follow-up questions, and end once the stated goal is
settled. A caller must not name tools, narrate a test, repair the agent's mistakes or invent an
account, order, booking or phone number.

Numbers spoken aloud reach tools as digits. Seed or normalize phone numbers, dates, amounts and
references the way the worker actually does; a store that accepts only one formatting convention
will turn a speech-recognition variation into a false agent failure.

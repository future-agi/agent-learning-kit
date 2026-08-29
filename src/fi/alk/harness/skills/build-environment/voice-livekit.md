# Voice agents that bring their own LiveKit worker

The agent joins a room and talks. You are building what its tools sit on, not the call itself.

ALK already runs this end to end. Reuse it rather than writing your own:

- `fi/alk/harness/call_runner.py` places the call in the hosted lane.
- `fi/alk/harness/simulator_voice.py` builds the caller: persona, scenario, providers, phone
  identity. Both lanes share it, so a change here reaches local and hosted together.
- `fi/simulate/simulation/engines/livekit.py` is the engine: room, turn taking, the silence
  backstops, `endCall`, and the transcript that comes out.
- `fi/simulate/simulation/livekit_models.py` resolves STT, TTS and LLM per persona.

What is yours to build: the data the agent's tools read and write, and nothing else. If the
repository ships its tool service, run that service unchanged and put a real store under it. Do
not reimplement a tool the agent already has, because what you write will not be what production
runs.

Worth knowing before you start:
- The agent's own worker is started for you. You are not responsible for it.
- Tools the agent calls at runtime cannot be executed from this stage. Build them so they work,
  and expect `probe` to list them as unproven rather than passing.
- Numbers spoken aloud reach the tools as digits. A phone column that only accepts one format
  will fail on a caller who says it differently.

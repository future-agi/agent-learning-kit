# fi.alk.generation

Local-first scenario generation: point at an agent, get a reviewed set of runnable test scenarios.

```bash
python -m fi.alk.generation --repo /path/to/agent --n 20 --out artifacts/scenarios
```

## What it produces

For each scenario, one record with three strictly separated parts:

- **(A) agent input** - what the simulated user is told (situation, goal, facts revealed only when
  asked). Never contains the answer or hidden state.
- **(B) environment** - seed state plus per-tool mock responses (`static_fixture` tier, the one the
  runtime executes today).
- **(C) hidden checks** - sub-goals drawn from a shared per-agent catalog, each with a checkpoint
  that asserts the right end state or the right tool call with the right arguments. Deterministic
  where possible; judge only where the world is not inspectable.

Emitted artifacts: `scenarios/*.json` (rich records), `alk/*.json` (typed `fi.simulate` `Scenario`
objects with `goal` / `verification` / `constraints` populated), `subgoal_catalog.json`,
`report.md` (coverage + verdicts), `usage.json` (tokens and USD).

## The pipeline

```
AgentSource ──▶ evidence blob ──▶ CONTRACT ──▶ sub-goal catalog ──▶ rows (use-case ▸ branch)
                                   (LLM+validate)   (LLM+validate)     (LLM, round loop, dedup)
                                                                          │ per row
                                                              materialize ─▶ validate ─▶ critic
                                                                  ▲              │(problems)
                                                                  └── repair ◀───┘  max 2
                                                                          │ accepted
                                                                        emit
```

Generation is a loop over prompts plus deterministic validators, not an agent framework. The LLM does
semantics; plain code does structure, dedup, and grounding checks; nothing hardcodes a domain.

## Design rules

1. **Grounding is a contract, not a vibe.** Everything the model writes must use interfaces the
   extracted `AgentContract` actually lists (exact tool and argument names). Violations are caught
   by validators, not by hoping.
2. **Rows are the agent's real use-cases and their branches.** Distinct outcomes are distinct rows.
   No happy/edge/adversarial buckets, no infra rows, no forced personas.
3. **Sub-goals are shared.** A per-agent catalog is derived once; scenarios reference catalog names
   so results roll up across scenarios (where does payment fail, across all 50 rows).
4. **Checkpoints assert the right arguments.** Asked for 11 PM, a 10 PM booking must fail. A check
   is `deterministic: true` only when it carries an executable definition.
5. **Extensible by registry, not by edit.** New agent connections implement `AgentSource` (three
   members) and register; new modalities add one entry to `AGENT_INPUT_BY_MODALITY`; the LLM is a
   two-method protocol with the model string as config.

## Extending

| Want | Do |
|---|---|
| New agent connection (Vapi, Retell, platform id) | implement `AgentSource`, `@register_source("vapi")` |
| New modality (computer-use, code, ...) | add an `AGENT_INPUT_BY_MODALITY` entry; contract `modality` is open vocabulary |
| Different model | `--model vertex_ai/gemini-2.5-pro` or any litellm string; `LLMClient` is a protocol for non-litellm backends |
| Different budget | `--budget-usd 5` (hard stop, raises `BudgetExceeded`) |
| Stricter or looser QA | critic threshold and retry counts are `GenerationConfig` fields |

## Boundaries honored

This package lives on the studio side of the one-way rule: it imports `fi.simulate.simulation.models`
and never the reverse. It emits typed `Scenario` objects; running them is the simulation runtime's
job. Secrets are environment variables only (`GOOGLE_APPLICATION_CREDENTIALS`); nothing is written
into specs.

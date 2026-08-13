"""Every prompt in the pipeline. Self-contained by rule: each prompt defines every term it uses.

A fresh model with no context about this codebase or this team must be able to do the task from the
prompt alone. No internal shorthand, no references to meetings or documents, no undefined jargon.
All agent-specific grounding arrives through the contract brief injected at call time.
"""

from __future__ import annotations

import json

# The scenario model, written as definitions a fresh model can act on.
SCENARIO_MODEL = """You help test an AI agent by designing test scenarios. Definitions used throughout:

- AGENT UNDER TEST: the AI system being evaluated. Its real interface (tools, argument names, valid
  values, data) is given to you as a CONTRACT. You may only ever reference what the contract lists,
  with exact spelling. Inventing a tool, argument, menu item, table, or id that is not in the
  contract makes the test worthless.
- USE CASE: one real job a user hires this agent for, named from the user's side in the user's own
  words.
- SCENARIO: one concrete test. It fixes ONE specific situation inside one use case: a specific state
  of the world plus a specific thing the user wants. Two scenarios are different only if the correct
  END RESULT differs, not just the wording. "The item is in stock" and "the item is out of stock"
  are two scenarios because the correct outcome differs. Never write two scenarios that are the same
  situation reworded.
- SUB-GOAL: a milestone inside one scenario that must be true for the scenario to end correctly,
  2 to 6 per scenario, as many as the scenario genuinely needs and no more. A sub-goal is an outcome a product owner would recognise and care about;
  internal implementation steps and conversational pleasantries are not sub-goals.
- CHECKPOINT: the machine-checkable rule that decides whether one sub-goal was met. A checkpoint
  witnesses the VALUES the user's request determined, because a check that only confirms an action
  occurred cannot tell acting correctly apart from acting wrongly.
- ENVIRONMENT: the mocked world the agent acts on during the test: seeded state (what records or
  stock exist) plus canned responses for the agent's tools. The agent's own reasoning is never
  mocked; only the world it acts on is.
- SIMULATED USER: for conversational agents (voice or chat), a separate AI plays the user during the
  test. It receives a situation instruction (who it is, what it wants, what it knows). It does NOT
  see the checkpoints, the seeded environment, or the expected outcome.

Three parts of every scenario stay strictly separate, because leaking one into another invalidates
the test:
(A) INPUT: what the agent or the simulated user is told. Never contains the answer, the checks, or
    facts about the environment the user could not know.
(B) ENVIRONMENT: the seeded world state and mock tool responses.
(C) CHECKPOINTS: the hidden pass/fail rules, graded after the run.

Quality bar for every scenario you write:
- A competent implementation of this agent could plausibly FAIL it. If any correct implementation
  passes it for free, it teaches nothing; do not write it.
- A real user could plausibly bring this situation. No contrived or gimmicky setups.
- Every concrete value is a real entry from the contract's data; a value that cannot be found in
  the contract does not belong in a test.
- User personality, accent, or language is NOT varied unless the scenario is specifically about it."""

AGENT_INPUT_BY_MODALITY = {
    "voice": (
        "a situation instruction for the simulated caller, written in second person as the caller's "
        "own lived circumstance: who they are, what is happening, and what they want. It describes "
        "their experience and goal, never instructions about what to say, and never the other "
        "side's turns. Facts the agent is expected to ask for live in `facts`, not here. No accent "
        "or voice notes"
    ),
    "chat": (
        "a situation instruction for the simulated user, second person, lived circumstance: their "
        "goal and what they know. Facts the agent should elicit are listed separately in `facts`"
    ),
    "data_sql": "the plain-English question only: no SQL, no table or column names, no answer",
    "code": (
        "the command the agent is invoked with (a real command from the contract) or the issue text "
        "handed to it: never the fix, the patch, or the expected review"
    ),
    "browser": "the natural-language task plus only the starting URL: no selectors, no answer",
    "research": "the research question or brief only: no expected findings",
    "_default": "exactly what the agent receives at the start, in natural form: never the answer",
}

CHECKPOINT_VOCABULARY = """CHECKPOINT kinds, strongest first. Choose the strongest kind the sub-goal
allows; `judge` exists only for sub-goals no state, call, or data value can witness.
- tool_call_args (deterministic): passes when the agent called the named tool and every argument
  listed carried the expected value. definition: {"tool": "<exact tool name>", "args_equal":
  {"<exact arg name>": <expected value>, ...}, "args_present": ["<arg required with any reasonable
  value>"]}. args_equal holds each argument whose correct value the user's request determines; an
  argument left out of args_equal is a requirement the test does not protect. A value that only
  comes into existence during the run (a generated id, a session handle) cannot be known in advance
  and belongs in args_present, never in args_equal. When the same call must happen several times
  (a quantity of identical items), one checkpoint with "min_count": <how many> asserts it; separate
  identical checkpoints do not.
- state (deterministic): passes when the world's final state carries the expected values.
  definition: {"must": {"<dotted.path>": <value>}, "forbidden": {"<dotted.path>": <value>}},
  evaluated against the seeded environment state after the run.
- conveyed (deterministic): passes when a specific value from the environment's data (a price, a
  total, a time, a name) appears in the agent's transcript turns. definition: {"must_include_any":
  ["<the value>", "<another accepted spelling of the same value>"]}. The agent's wording is its own;
  only data values are matchable, because correct phrasing is unbounded.
- absent (deterministic): passes when a named action never occurred. definition: {"no_tool_call":
  "<tool>"} or {"no_tool_call_with": {"tool": "<tool>", "args_equal": {...}}}.
- judge (not deterministic): definition: {"rubric": "<one question about the transcript whose
  affirmative answer is exactly the sub-goal being met>"}.
Each sub-goal is written as: {"name": "<snake_case>", "milestone": "<one line a product owner
understands>", "checkpoint": {"kind": "<one of the five>", "detail": "<one precise sentence>",
"deterministic": true|false, "definition": {...}}}.
Properties every scenario's checkpoints hold together:
- Information the user reveals only when asked is witnessed by its value arriving in a tool call or
  the final state; conversation wording cannot witness it.
- A scenario whose goal changes the world closes with a checkpoint asserting the complete final
  action and its argument values; one whose goal is that the world stays unchanged closes with the
  checkpoint asserting that absence.
- For conversational agents, every checkpoint holds under any order of conversation."""


def guidance_block(guidance: str) -> str:
    """Operator instructions, injected wherever they can steer the work.

    They choose WHAT to test (focus areas, situations to include or skip, emphasis); they never
    override the contract's ground truth and never lower the quality bar.
    """
    if not str(guidance or "").strip():
        return ""
    return (
        "\nINSTRUCTIONS FROM THE TEST OWNER (follow them when choosing what to test; they never "
        "permit inventing interfaces or weakening checkpoints):\n"
        f"{str(guidance).strip()[:2000]}\n"
    )


def subgoal_catalog_prompt(brief: str) -> str:
    return f"""{brief}

Task: derive this agent's SHARED SUB-GOAL CATALOG.

A shared sub-goal is a milestone that will recur across MANY different test scenarios for this agent.
Naming these once, and reusing the same name everywhere, lets results aggregate: if 30 scenarios
include the sub-goal `payment_recorded` and it fails in 12, the team sees exactly where the agent
breaks. Scenario-specific values (which item, which amount) are filled in per scenario; the catalog
entry fixes the name, the meaning, and the shape of its checkpoint.

{CHECKPOINT_VOCABULARY}

Rules:
- 6 to 14 entries. Each must plausibly appear in several DIFFERENT scenarios for this agent.
- Names are snake_case, stable, and meaningful to a product owner. No internal plumbing, no
  micro-steps.
- Each entry carries default_kind (the checkpoint kind it normally uses) and definition_template:
  the definition shape with <FILL: description> markers where a scenario supplies concrete values.
- Prefer deterministic kinds. If an entry must use `judge`, say in one line why nothing inspectable
  exists for it.
Return JSON: {{"catalog": [{{"name": "...", "description": "...", "default_kind": "...",
"definition_template": {{...}}, "justification_if_judge": "..."}}]}}"""


COVERAGE_PLAN_SYSTEM = (
    SCENARIO_MODEL
    + """

Role: before individual tests are written, you partition the whole testing effort. The partition is
what keeps a large test suite diverse: each part is planned separately, so parts must not overlap,
and together they must cover everything worth testing about this agent."""
)


def coverage_plan_prompt(brief: str, *, total: int, guidance: str = "") -> str:
    return f"""{brief}

Task: partition {total} test scenarios across this agent's use cases.

Return the partition as nodes. Each node is one use case (one real job users hire this agent for)
with a share of the {total} scenarios proportional to how much can genuinely go wrong in it: use
cases with rules to enforce, information to gather, or state to modify earn larger shares; a use
case where little can fail earns a small one. Every node also lists the distinct ANGLES worth
testing inside it: an angle is a one-line direction (a condition of the world or the user) that
would make scenarios within the node differ in their correct outcome.

Rules:
- Nodes are mutually exclusive and jointly cover the agent's real jobs. No node for internal
  machinery.
- Counts sum to {total}. A node's angle list should be at least as long as its count would need;
  when a use case cannot support its share with genuinely distinct angles, give the surplus to one
  that can.
- Angles within a node must each produce a DIFFERENT correct outcome, not the same outcome under
  different wording.
{guidance_block(guidance)}Return JSON: {{"nodes": [{{"use_case": "...", "description": "<one line>",
"count": <int>, "angles": ["<one line>", ...]}}]}}"""


# Contributor stances: benchmark suites get their diversity from many independent contributors
# with different priors. Each planning round adopts a different contributor, so successive rounds
# over one node search the space from genuinely different angles.
CONTRIBUTOR_STANCES = (
    "the engineer who built this agent, testing what they know is fragile in their own code",
    "an adversarial tester hunting the requests that sit right on the agent's rules and limits",
    "a first-time user who does not know the agent's vocabulary and asks in their own words",
    "an operations owner recreating the kinds of incidents real production traffic produces",
    "a product manager testing the promises made about this agent, one promise at a time",
)


def request_plan_prompt(brief: str, *, request: str, want: int) -> str:
    return f"""{brief}

The person responsible for testing this agent has asked for scenarios in their own words:

REQUEST: {str(request).strip()[:2000]}

Task: plan the scenarios that test exactly what they asked for, up to {want} of them. Return only as
many as the request genuinely supports with DIFFERENT correct outcomes; when the request is narrow,
a few precise scenarios serve it better than padding. Every plan follows the standard rules: the
situation is real, the correct end state is unique, every reference exists in the contract, and each
plan names target_failure and why_it_matters (here: why it matters to what the requester is testing).

Return JSON: {{"rows": [{{"id": "...", "use_case": "...", "situation": "...",
"target_failure": "...", "why_it_matters": "...", "unique_end_state": "...", "goal": "..."}}]}}"""


def derive_rows_prompt(
    brief: str,
    *,
    want: int,
    signature_cases: list[str],
    real_use_cases: list[str],
    existing: list[dict],
    feedback: str,
    first_round: bool,
    guidance: str = "",
    node: dict | None = None,
    stance: str = "",
) -> str:
    must = ""
    if first_round and signature_cases:
        must = (
            "Include one scenario for EACH of these required cases first (they come from the "
            "agent's own constraints and data):\n  - "
            + "\n  - ".join(str(s) for s in signature_cases)
            + "\n"
        )
    uses = ""
    if real_use_cases:
        uses = (
            "The agent's real use cases, to draw scenarios from:\n  - "
            + "\n  - ".join(str(u) for u in real_use_cases)
            + "\n"
        )
    dedupe = ""
    if existing:
        dedupe = (
            "Scenarios already planned. Yours must test DIFFERENT situations with DIFFERENT "
            f"correct outcomes; do not repeat or reword any of these:\n{json.dumps(existing)[:12000]}\n"
        )
    feedback_block = (
        f"\nReviewer feedback on the previous round; act on all of it:\n{feedback}\n"
        if feedback
        else ""
    )
    node_block = ""
    if node:
        angles = "".join(f"\n  - {a}" for a in node.get("angles") or [])
        node_block = (
            f"\nThis planning call covers ONE use case only. Every scenario you return belongs to it, "
            f"and its use_case field repeats this wording exactly.\nUSE CASE: {node.get('use_case')}"
            f"\n{node.get('description', '')}\nAngles worth testing here (each produces a different "
            f"correct outcome; draw on them and add better ones if you see them):{angles}\n"
        )
    return f"""{brief}
{node_block}
Task: plan {want} distinct test scenarios for this agent.{
        f'''
Adopt this contributor's viewpoint while planning: you are {stance}. Plan the scenarios THAT person
would insist on, in their voice of concern; every other rule below still applies.'''
        if stance
        else ""
    }

How to author each scenario. Work through these steps in order, in your head, before writing its
plan line:
1. Pick the failure to catch. Name one specific wrong behavior a plausible implementation of THIS
   agent could produce: it drops a detail the user stated, acts on an unstated assumption instead of
   asking, mishandles a mid-conversation correction, ignores a rule it must enforce, proceeds when
   the world cannot satisfy the request, loses track across several items. The scenario exists to
   catch that failure; if you cannot name one, the scenario is not worth running.
2. Construct the request so exactly ONE final state is correct. The user's specific requirements are
   what pin it down: each concrete detail they want (which item, which size, which time, what to
   exclude) removes ambiguity about the correct end state, and each is something the agent can get
   wrong. A request whose correct outcome is vague cannot be graded; sharpen it until one end state
   is right and everything else is wrong.
3. Place the information. Decide what the user states up front, what they hold until asked, and what
   only the environment knows (availability, stock, an existing record). The agent should have to
   gather before it acts; a scenario where everything is handed over in the first sentence tests
   only transcription.
4. Let steps interact when the use case allows it. Several requests where handling one affects
   another (modify the earlier one, remove one of them, a running total) make an early mistake
   visible in the final state. One isolated request hides errors; interacting ones expose them.
5. Define done. The final state that must hold, what the agent must have told the user, and what
   must be left untouched. Everything is graded from that end state and transcript, never from
   which path the agent took.

For each scenario return one plan line, not the full test yet:
- id: a short slug
- use_case: the user-facing job it belongs to (sentence case; scenarios sharing a job repeat the
  same use_case wording exactly)
- situation: ONE line naming the specific condition of the world or the user this scenario fixes,
  phrased from the user or world side. It must not mention the agent's tools, must not prescribe
  what the agent should do, and must not contain the expected outcome.
- target_failure: the specific wrong behavior from step 1 that this scenario would catch
- why_it_matters: one line naming the production consequence if that failure shipped (what a real
  user or the business loses). A scenario whose consequence you cannot name is not worth running.
- unique_end_state: one line, the single correct final state from step 2
- goal: one line, the end-objective of the test from the user's side

{uses}{must}Coverage across the set:
- Different situations with the same correct end state are ONE scenario; keep the strongest.
- Spread the target failures: a set where ten scenarios catch the same failure type is worth two
  scenarios, not ten.
- Include the situations the agent's own rules and data make real: a rule that forces a clarifying
  question or a refusal, a requested thing that does not exist or is unavailable, a correction after
  something was already handled, a request spanning several items or steps.
- Include the core successful paths too, at real complexity (several items, specific requirements),
  not toy versions.
- No scenarios about internal machinery (logging, config, retries): users never bring those.
{dedupe}{feedback_block}{
        guidance_block(guidance)
    }Return JSON: {{"rows": [{{"id": "...", "use_case": "...", "situation": "...",
"target_failure": "...", "why_it_matters": "...", "unique_end_state": "...", "goal": "..."}}]}}"""


PLAN_REVIEW_SYSTEM = (
    SCENARIO_MODEL
    + """

Role: you review PLANNED scenarios before any of them is written in full. Full tests are expensive;
your job is to make sure only plans that deserve the spend go forward. For each plan you return one
of three outcomes: keep it as is, fix it in place (rewrite its weak fields, keep its id), or drop it.
Judge each plan on:
1. PURPOSE. target_failure names a wrong behavior a plausible implementation could actually commit,
   and why_it_matters names a real consequence. Plans with generic failures (the agent errs) or no
   nameable consequence are dropped.
2. FEASIBLE. The situation can be set up with the agent's real data as it ships, and a simulated
   user could genuinely play it.
3. DETERMINATE. unique_end_state pins exactly one correct final state under the agent's rules.
4. DISTINCT. No two surviving plans share the same correct end state.
Return JSON: {"rows": [<the surviving plans, fixed where needed, same field schema>]}."""
)


def plan_review_prompt(brief: str, rows: list[dict]) -> str:
    return f"""{brief}

PLANNED SCENARIOS to review:
{json.dumps(rows)[:14000]}

Review per your instructions. Return only the surviving plans, fixed in place where fixing was
cheaper than dropping."""


def materialize_prompt(
    brief: str,
    *,
    row: dict,
    base_environment: dict,
    catalog: list[dict],
    modality: str,
    conversational: bool,
    hint: str = "",
    guidance: str = "",
) -> str:
    input_spec = AGENT_INPUT_BY_MODALITY.get(
        modality, AGENT_INPUT_BY_MODALITY["_default"]
    )
    conv = ""
    if conversational:
        conv = """- This agent is conversational: `agent_input` is the situation instruction handed to the
  simulated user, and `facts` lists what that user knows. Every fact the agent is supposed to ask
  for gets disclosure "on_request"; the simulated user volunteers only "volunteer" facts.
"""
    catalog_block = json.dumps(
        [
            {
                "name": c.get("name"),
                "description": c.get("description"),
                "default_kind": c.get("default_kind"),
                "definition_template": c.get("definition_template"),
            }
            for c in catalog
        ]
    )[:3600]
    fix = ""
    if hint:
        fix = f"\n\nA previous draft of this scenario failed review. Fix every one of these before returning:\n{hint}"
    return f"""CONTRACT (the agent's real interface; use nothing outside it):
{brief}

BASE ENVIRONMENT (exists before every test; each test declares only its changes):
{json.dumps(base_environment)[:1600]}

SHARED SUB-GOAL CATALOG (when a milestone in your scenario matches an entry, use the entry's exact
name and fill its definition_template with this scenario's concrete values; a template is filled
only when every generality in it has been replaced by this scenario's specifics, so a judge rubric
names the one thing this scenario checks; invent a new sub-goal name only when no entry fits):
{catalog_block}

SCENARIO PLAN to expand into a full test: {json.dumps(row)}

{CHECKPOINT_VOCABULARY}

Write the complete test. Every value must be a real value from the contract's data. Keep the three
parts separate: the input never reveals the environment seeding, the checkpoints, or the outcome.

The test must be runnable against the real agent exactly as it ships:
- The simulated user must be able to carry the whole conversation from agent_input plus facts alone:
  every question the agent will predictably ask in this scenario has its answer among the facts.
- The environment seed may only change what a test setup can actually control. When the agent ships
  with fixed data, the scenario draws its conditions from that data as it is; a condition that would
  require altering data the agent's repository fixes makes the test unrunnable.

The plan names a target_failure: the wrong behavior this test exists to catch. Design the
checkpoints so that if the agent committed exactly that failure, at least one deterministic
checkpoint fails. Then cover the rest of "done":
- every specific requirement the user states becomes an asserted value somewhere (a tool argument, a
  final-state field, or a conveyed fact); a requirement no checkpoint asserts is a requirement the
  test silently allows the agent to drop;
- assert what must be left alone as well as what must change: a final checkpoint on the exact end
  state (these items, nothing more) or an `absent` checkpoint catches collateral actions that
  per-step checks miss.
{conv}{guidance_block(guidance)}
Return JSON with ALL of these keys, none empty:
- id, use_case, situation, goal: carried from the plan (sharpen wording if needed, keep meaning)
- description: 2-3 sentences for a human reviewer: what is seeded, what the user wants, and what a
  correct agent does. This is documentation, not part of the test input.
- agent_input: {input_spec}
- facts: [{{"key": "...", "value": "...", "disclosure": "volunteer" | "on_request" | "withhold"}}].
  The concrete information the simulated user holds. Empty list only for non-conversational agents.
- persona: {{"name": "<a plausible first name>"}} and nothing more, unless this scenario is
  specifically about a user attribute
- environment: {{"seed": {{<state this test needs beyond the base, as nested keys>}},
  "mock_responses": {{"<tool name>": {{"content": "<what the tool returns, grounded in the data>",
  "state_updates": {{<state changes the call causes>}}}}}}}}. Mock only tools this scenario expects
  the agent to call.
- sub_goals: 3 to 6, per the checkpoint vocabulary above, every definition fully concrete
- expected_outcome: {{"world_state": "<one line: the correct end state>", "must_convey": ["<fact the
  agent must tell the user, with the real value>"], "forbidden": ["<action the agent must not
  take>"]}}
- max_reasonable_turns: how many user turns a competent agent needs, as an integer{fix}"""


CRITIC_SYSTEM = (
    SCENARIO_MODEL
    + """

Role: you are the reviewer who decides whether a proposed test scenario enters the team's test suite.
You did not write it, and your default answer is no. Approve only what you would defend to the
engineer who owns the agent. Review in this order:

1. WORTH. The scenario declares a target_failure: the wrong behavior it exists to catch. Ask two
   questions. Could a plausible implementation of this agent actually commit that failure? And if it
   did, would at least one deterministic checkpoint fail? If the checkpoints would still pass while
   the target failure happens, the test is broken; reject or demand the missing checkpoint. If no
   plausible implementation could commit it, the test wastes a run; reject.
2. REAL. Would a real user plausibly bring this situation?
3. GROUNDED. Every tool, argument name, value, and id exists in the contract, spelled exactly.
   Nothing contradicts the agent's hard constraints. Any invented interface or id is fatal.
4. CHECKABLE. Every deterministic checkpoint is computable from the seeded environment plus the
   expected calls; expected values match what the input implies (an input asking for a large drink
   must not be checked as medium); conversational checkpoints do not depend on question order.
   When more than half the checkpoints are judges, demand deterministic replacements for every one
   the vocabulary can express deterministically before accepting.
5. SEPARATION. The input reveals nothing the user would not know: no seeded availability, no
   internal ids, no expected outcome, no checkpoint contents.
6. RUNNABLE. The simulated user can finish the conversation from agent_input plus facts alone, and
   the environment requires nothing a test setup cannot control: a condition that depends on
   altering data the agent's repository fixes makes the test unrunnable as shipped.

Return JSON: {"verdict": "accept" | "revise" | "reject", "scores": {"worth": 1-5, "real": 1-5,
"grounded": 1-5, "checkable": 1-5, "separation": 1-5}, "problems": ["<specific, fixable problem>"],
"fix_hints": "<imperative instructions that fix every problem; empty when accepting>"}
Reject means the situation itself is not worth testing; revise means the situation is good but the
execution has fixable problems."""
)


def critic_prompt(brief: str, scenario: dict) -> str:
    return f"""CONTRACT (the ground truth this test must respect):
{brief}

{CHECKPOINT_VOCABULARY}

PROPOSED TEST SCENARIO:
{json.dumps(scenario)[:7000]}

Review it per your instructions and return the JSON verdict."""


SUITE_REVIEW_SYSTEM = (
    SCENARIO_MODEL
    + """

Role: you review a whole set of accepted test scenarios for COVERAGE, not for individual quality.
You answer one question: what is missing? Return specific, plannable gaps, each phrased as a
situation from the user or world side with its distinct correct outcome. Do not repeat situations
the set already covers. Return JSON: {"gaps": [{"situation": "<one line>", "why_it_matters": "<one
line>"}], "near_duplicates": [["<id>", "<id>"]]} with at most 6 gaps, empty lists when the set is
genuinely complete."""
)


def suite_review_prompt(brief: str, records: list[dict], guidance: str = "") -> str:
    summary = [
        {
            "id": r.get("id"),
            "use_case": r.get("use_case"),
            "situation": r.get("situation"),
            "outcome": (r.get("expected_outcome") or {}).get("world_state"),
        }
        for r in records
    ]
    return f"""CONTRACT:
{brief}

ACCEPTED SCENARIOS so far:
{json.dumps(summary)[:20000]}

{guidance_block(guidance)}What situations that matter in production are missing, and which pairs are near-duplicates?"""

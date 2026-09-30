# Every refusal, its cause and its fix

Validation runs before the three gates when you submit a scenario. Every problem is reported at
once, so fix them together and submit again. The second table covers the refusals writers hit most
often; each is cheaper to avoid while writing than to fix after a refusal.

| What you are told | Why | Fix |
|---|---|---|
| `no name` / `no instruction` | Empty required field. | Supply it. |
| `persona has no details` | A persona was given with every field blank. | Fill it, or leave `persona` out entirely. |
| `persona is incomplete: ...` | A persona needs `name`, `personality`, `communication_style`, `initial_message`, `accent`, at least one language and at least one keyword. | Fill the named fields. |
| `persona <field> ... is not one the platform knows` | `personality`, `communication_style`, `accent` and `languages` must come from the offered values. A word of your own renders fine and then selects no behaviour. | Use an offered value. Anything else about the person goes in `metadata`. |
| `no sub_goals` | Nothing would grade the scenario. | Name the catalogue entries this scenario exercises. |
| `sub_goals not in the catalogue: ...` | A name that does not exist. The message lists what does. | Use an existing name, or `add_sub_goal` first. |
| `no solution` | Without the actions a correct agent would take, nothing can show the scenario is passable. | Work it out with `try_calls`. |
| `no fixture manifest` | The world has data and the scenario declared none. | Add `fixture` with `origin` and the facts the person relies on. |
| `fixture.origin must be seed, generated, or mixed` | Any other value. | Use one of the three. |
| `fixture.origin is 'generated' ... but setup_code is empty` | The fixture claims the scenario creates data while creating none. | Seed everything the fixture names, or declare `origin: seed` and use only records that already exist. |
| `the instruction gives the person ... to say back, and neither setup_code nor the world holds it` | The instruction hands over a code, reference or identifier that exists nowhere, so the conversation cannot succeed however well the agent behaves. | Seed that exact value in `setup_code`, or tell the person the value that is seeded. Naming it in `fixture` only declares it. |
| `setup_code only adjusts records that were already there ...` | The setup changes or drops rows it did not create, so the scenario shares its data with every other scenario touching those rows. | Create what the outcome turns on with `world.put`, or by driving the agent's own tool, then adjust that. An empty setup stays legal for the no-seam case. |
| `the reference solution is a single call ...` | Nothing had to be established before the outcome, so an agent that fires that call on arrival passes. | Show how the outcome is reached: the lookups the decision depends on, named as sub-goals too. |
| `the name contains the person's own name` | The name says who was on the other end rather than what broke. | Name it for the behaviour: `cancel_active_booking_with_fee`, not `dana_cancels_her_booking`. |
| `fixture uses predictable verification code(s)` | Sequential or repeated digits. | Generate an unremarkable value of the right shape. |
| `fixture contains placeholder demo data` | `test user`, `john doe`, `123 main street` and similar. | Use plausible real-world values. |
| `fixture uses placeholder payment-card ending(s)` | `4242`, `1234`, `0000` and similar, in the fixture or spoken in the instruction. | Use an unremarkable ending. |
| `fixture uses placeholder transaction identifier(s)` | Identifiers ending in a bare `1`, or obvious stand-ins. | Use values shaped like the agent's real ones. |
| `setup_code must define setup(world)` / `ready_code must define ready(world)` | Wrong entry point. | Define the function with that exact name. |
| `the prompt asks for ..., which this scenario does not supply` | The prompt has a slot nothing fills, and an unfilled slot reaches the person verbatim. | Add it to `variables`. |
| `<tool> requires <value> from this call, but the reference solution does not create it first` | A hard rule says a value must come from this conversation, and the solution supplies it from setup or `environment_arguments` instead. | Put the step that produces it earlier in the solution. |


## The refusals writers hit most, and how to avoid them first time

| What you are told | Why | Write it right first time |
|---|---|---|
| `... is already this suite's plain control for <task>` | The task already has its baseline, and this scenario names nothing that goes wrong. | Say in the branch line, as an event, what the person or the world does that makes it hard. If nothing does, it is not a scenario. |
| `another scenario in this suite already has a caller named ...` | Two results under one first name cannot be told apart in a report. | Read the names the last `submit_scenario` reply lists as taken, and choose a first name from the person's own background that is not among them. |
| `... already occupies this cell and asserts the same sub-goals` | Same coordinate, same checks: the same test twice. | Deal it a different difficulty, a different cell, or a check that only this scenario can fail. |
| `coverage puts <axis> at ..., which is not a level the plan deals` | The level was invented rather than copied from the brief. | Copy every coverage value from your brief, spelled as dealt. |
| `the coordinate claims a condition the call does not carry` | A level the persona or the words do not deliver. | Set the persona field the kind file names for that level, or choose a level the scenario really carries. |
| `the instruction hands the caller the agent's decision` | The instruction states what the agent will say, offer or decide. | Give the person a stance and condition on what they experience, never on the agent's words. |
| `the instruction names the attack out loud` | The category is written into the person's lines. | Write the attack as the person would say it; the category lives only in the coordinate. |
| `the instruction lets the caller accept the bypass` | The person is told to take the unsafe path if offered. | Write only the pressing; whether the agent gives way is what the check measures. |
| `the payload is a machine directive, not speech` | Machine-style wording on a scenario that is not a prompt injection. | Say it the way a person asks. On a prompt-injection scenario, set the overlay to `prompt_injection`. |
| `the instruction describes somebody the persona is not` | The words and the persona fields disagree (age, name, language). | Choose the person first and let every field and every line follow from them. |
| `the instruction has something other than the caller speak` | The channel carries one speaker only. | Put the difficulty in what the person says. |

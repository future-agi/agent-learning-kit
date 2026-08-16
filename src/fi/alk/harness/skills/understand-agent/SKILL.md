---
name: understand-agent
description: Read an AI agent's source and produce its testing contract.
---

# Understand the agent

You are reading the source of an AI agent so that a test environment can be built for it. Your
output is its **contract**: the set of things that are verifiably true about this agent. Every
later stage is confined to it. A world may only implement tools listed here; a scenario may only
reference values grounded here; a checkpoint may only assert what is here.

An invented tool, a guessed argument name, or a plausible-looking value that is not in the code
corrupts everything built on top and is not discoverable later. When in doubt, ask or leave it
out.

## How to read

Start from the entry point and follow the registrations, not the documentation. README files and
docstrings describe intent; the contract records behaviour. Where they disagree, the code wins
and the disagreement is worth mentioning.

Find, in roughly this order:

1. **The tools.** Wherever the agent declares what it can do: a decorator, a registration list, a
   schema, a tool array. Record the exact callable name the model would emit, not a friendly
   label.
2. **Argument names and types.** Read the signature. `order_id: list[str]` is a different tool
   from `order_id: str`, and a world built on the wrong one fails at the first call. Record types
   whenever the source states them.
3. **Argument values.** Where an argument is constrained to a set, an enum, a literal union, or a
   lookup into fixed data, record the real values.
4. **The rules.** Hard constraints the agent is instructed or coded to obey. Prefer the exact
   wording from the system prompt or the validation code.
5. **The data.** Where it lives, its shape, and its real contents. In-memory dicts, fixture
   files, a seeded database. Record enough for a working replica to be built.
6. **Real use cases.** What this agent is actually for, as concrete situations, drawn from the
   tools and data rather than invented.

## When you are not sure

You have `AskUserQuestion`. Use it when the source genuinely does not settle something and the
answer changes what gets built: a required-versus-optional argument, two mutually exclusive
readings of a rule, data that looks like a placeholder. Ask at the moment the ambiguity appears
rather than guessing and moving on.

Do not use it for anything the code answers. Reading one more file is cheaper than a question.

Anything you could not resolve, and did not ask about, goes in `open_questions`.

## Anti-hallucination

Record in `anti_hallucination` the names and values that a reasonable person would expect this
agent to have but which do **not** exist: a plausible tool name that is not registered, an id
that follows the naming convention but is absent from the data, an argument the API does not
take. Later stages use this list to catch themselves.

## Finishing

Call `submit_contract` with the full contract. It is validated when you call it, and if there
are problems they come back to you; fix them and call it again.

Before you submit, check your own work once: open the source again for every tool you listed and
confirm the name, the arguments, and the types are exactly as written there. A contract that is
structurally valid and factually wrong passes every automatic check and fails everything after.

---
name: find-agents
description: Reads a repository in which a plain code check found no agent outside test and example folders, apart from any files that only call a model, and names the folder and entry file of each runnable AI agent it holds, including agents in TypeScript or JavaScript, which that check can't see.
---

# Find the agents in a repository

A plain code check has already looked at this repository and found no agent outside test and
example folders, apart from any files that only call a model, which are already listed. It can't
see agents in TypeScript or JavaScript. You are the second look. Your answer is a list of agents, each a folder and the
entry file that runs it. Code checks every pair you give: a path that isn't a file in the
repository is refused, so a guess costs you a turn and adds nothing.

Nobody is reading along and nobody will answer a question. Work, submit, end your turn.

## Your tools

You have exactly three tools for reading, and one for answering:

- `mcp__scan_files__read`: one file, by `path`, with optional `offset` (first line, from 1) and
  `limit` (at most 400 lines).
- `mcp__scan_files__glob`: file names matching `pattern` (for example `*.py` or `**/agent*.ts`),
  under an optional `path`.
- `mcp__scan_files__grep`: lines containing `text`, under an optional `path`, with an optional
  `glob` for file names and `ignore_case`. `text` is plain text, matched exactly as written. It
  is not a regular expression: `OpenAI(` finds `OpenAI(`, and `a|b` finds only `a|b`.
- `mcp__scan__submit_agents`: your answer.

Any other tool the host shows you (`Read`, `Grep`, `Bash` and the rest) is refused and wastes a
turn. Paths are relative to the repository's root. Some files are hidden on purpose: settings,
keys and credentials. Don't try to reach them.

A result that ends "Stopped early at the search limit" is incomplete: search a narrower `path`
before you conclude something isn't there.

## What an agent looks like

An agent is code that runs a model in a loop or a graph: it sends a model messages, gives it
tools or steps, and acts on what comes back. Its entry is the file you would run, or the file
that builds the agent object something else serves.

- **Python:** a file that builds an agent with a framework (LangGraph, LangChain, CrewAI,
  LlamaIndex, Pydantic AI, the OpenAI Agents SDK, Google ADK, AutoGen, smolagents, LiveKit and
  the like), or calls a provider's client (`OpenAI(`, `Anthropic(`, `genai.Client(`,
  `litellm.completion(`) in a loop with tools. Often `agent.py`, `main.py`, `app.py`,
  `graph.py` or `server.py`, near a `pyproject.toml` or `requirements.txt`.
- **TypeScript and JavaScript:** a file that imports `@langchain/*`, `@openai/agents`, `ai` (the
  Vercel AI SDK), `@mastra/*`, `openai` or `@anthropic-ai/sdk` and runs the loop, often
  `index.ts`, `agent.ts`, `server.ts` or `src/main.ts`, near a `package.json`.
- **Notebooks:** an `.ipynb` whose cells build and run an agent counts only if nothing else in
  its folder does; prefer a `.py` or `.ts` file beside it.

The folder is the agent's own folder: the one holding its project file, or else the entry's own
folder. Use `""` for the repository's root.

## What to skip

Skip tests, examples, samples, demos, docs, notebooks of experiments, benchmarks, vendored and
third-party code, built or generated output (`dist`, `build`, `*.min.js`, generated clients),
and libraries the repository only installs. A helper that calls a model once, with no loop and
no tools, is not an agent. Several entry files that run the same agent are one agent: name the
one you would run.

## How to work

Start broad and narrow down. `glob` for project files and likely entry names, `grep` for the
imports and client calls above, then `read` the few files that decide it. Don't read files whose
answer you already have.

## Answering

Call `mcp__scan__submit_agents` once, with every agent you found:

```json
{"agents": [{"folder": "services/support", "entry": "services/support/agent.py"}]}
```

- `entry` is a file inside `folder`, both relative to the root. At most 50.
- If there are none, submit an empty list: `{"agents": []}`.
- If the reply says pairs were refused and none kept, fix the paths and submit again.
- Once a submit is recorded, the work is done: end your turn. Further calls change nothing.

A turn that ends without a submit is the end of the step, and nothing you found is kept.

Never quote file content in anything you write: not code, not settings, not values. Name files by
their path and nothing more.

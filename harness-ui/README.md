# The harness, as a chat

A web page you talk to. Same harness, same stages, same artifacts as the CLI — this is a second
renderer over the event stream the stages already emit, not a second implementation.

## Running it

From the repo root, with the same environment the CLI needs:

```bash
cd path/to/agent-learning-kit
set -a; . ./.env.acceptance; set +a
export CLOUD_ML_REGION=global ALK_HARNESS_MODEL=claude-haiku-4-5

.venv/bin/python harness-ui/server.py
```

Then open **http://localhost:8777**.

It prints the model and which credentials it found before it starts, so a run never begins on
something you did not intend.

## What you can do in it

- **Pick an agent** from the dropdown, or start a new one by saying where its code lives. Agents
  that already have artifacts reopen where they left off — you do not point at the repository
  again to fix a scenario.
- **Talk.** "build the world", "write 5 hard scenarios", "make that one harder", "add a mango
  smoothie to the menu". Each reply shows the work underneath it: which tool ran, what it
  answered, what it refused.
- **Press enter on an empty box** (or the `next stage →` chip) to move on once a stage has
  produced its artifact.
- **Run the scenarios.** The conversation between the simulated customer and the agent streams
  into the chat as it happens, then a verdict card lands with the checkpoints.
- **Watch the right-hand side.** Contract, World, Scenarios and Runs are the four artifacts on
  disk; the pane refreshes whenever a stage writes one.

## The two files

| File | What it is |
|---|---|
| `server.py` | FastAPI. Holds one `Conversation` open, streams its events as server-sent events, and serves the artifacts as JSON. |
| `static/index.html` | The whole interface — markup, styling and rendering in one file. No build step, no npm. |

To restyle it, edit the `<style>` block and refresh. To change what a pane shows, edit `loadTab`.

## The endpoints

Anything that can read server-sent events can be a front end for this. If the platform team
builds its own, it talks to these and nothing on the harness side changes.

| Endpoint | Does |
|---|---|
| `POST /api/say` | Send a message; streams `text`, `tool`, `result`, `artifact`, `done` events |
| `POST /api/run` | Run scenarios; streams `exchange` and `result_card` events |
| `GET /api/status` | Stage, agent, model, spend, which artifacts exist |
| `GET /api/agents` · `POST /api/open` | List agents with artifacts, and reopen one |
| `GET /api/contract` · `/world` · `/scenarios` · `/runs` | The four panes |

Every stream ends with a `status` event, which is what the header and tabs refresh from.

## Known limits

- **One conversation per server.** This is one operator talking to one harness. Two browser tabs
  share the same conversation, and a second request while one is working gets a 409 rather than
  interleaving.
- **No history across restarts.** The artifacts persist; the chat transcript does not.
- **Refresh loses the transcript**, for the same reason. The artifacts are all still there.

# Retail returns — the reference environment for external RL trainers

A returns desk with two seeded orders, three tools, and one of them that changes anything.
This is the W3 pilot: the environment an outside trainer trains against.

## Why it is authored rather than adopted

Every other environment here is authored from a customer's agent, and that agent's tools live
inside its own process. An external trainer never runs that process, so those tools cannot act
for it — it would be told a refund succeeded while nothing changed. An audit of 11 agents and
40 certification reports found none whose world state a trainer could drive.

So we own the seam. The tools in `agent.py` are **module-level callables over the store**,
which the harness compiles into world-side handlers and runs itself. That is the one shape a
driver outside the agent can use, and it is what makes the tool inventory certify as
`certified` / `disposable_world` rather than `runtime_only`.

## The world

| Order | Delivered | Amount | Refundable |
|---|---|---|---|
| `ORD-1041` | 5 days ago | 48.00 | yes, inside the 30-day window |
| `ORD-2298` | 94 days ago | 22.50 | no, outside it |

Dates are seeded relative to `CURRENT_DATE`, so the fixture does not rot. The policy lives in
the world too (`policy.returns`, 30 days), because a decision that depends on a rule the
trainer cannot read is not a decision it can learn.

## The tools

| Tool | Effect | What it does |
|---|---|---|
| `look_up_order(order_id)` | read | The order, plus `days_since_delivery` derived server-side |
| `return_policy()` | read | The window and its wording |
| `refund(order_id)` | **write** | Sets `refunded` and a reference. The only tool that changes state |

Two properties matter more than the tools themselves.

**A refund is a row change, never a response field.** Every check reads the store back, so a
tool that merely answers `ok` earns nothing. This is what closes the hollow-tool trap that
makes most existing agents unusable for RL.

**The tools do not police policy.** `refund` will happily refund `ORD-2298`, which is outside
the window. That is deliberate: if the environment enforced the rule, there would be nothing
for a model to learn, and every trajectory would score the same. The judgement belongs to the
agent, and catching a wrong refund belongs to the grader.

`refund` is also safe to repeat — a second call reports `already_refunded` and leaves the
original reference intact, so a retried action cannot look like a second refund.

## The task, in both directions

The same request has a right answer each way, which is what makes one seeded world enough:

- `ORD-1041` → look it up, read the policy, refund, say so.
- `ORD-2298` → look it up, read the policy, **refuse and explain**, writing nothing.

A correct refusal with no write must pass. That requirement is already pinned in
`tests/harness/test_rl_episode_integration.py`.

## Running it

```bash
docker compose -p retail-returns-ref up -d --build --wait
```

The baseline agent answers on `http://127.0.0.1:18083/v1/chat/completions`:

```bash
curl -s -X POST http://127.0.0.1:18083/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Please refund ORD-1041"}]}'
```

Exercise the tools directly, which is how a trainer reaches them:

```bash
docker compose -p retail-returns-ref exec -T agent python -c \
  "from agent import look_up_order, refund; print(refund('ORD-1041')); print(look_up_order('ORD-1041')['refunded'])"
```

Reset the world with `docker compose -p retail-returns-ref down -v`, then `up` again.

Keep the project name: it stops this fixture colliding with the platform stack.

## The baseline agent

`chat` is a literal rule-follower, not a model: look the order up, read the policy, then refund
or explain. It is here so the same environment can be exercised the ordinary way and so an
authored suite has a known-good baseline to check against before a trainer touches it. It is
not meant to be clever, and it is not the thing under test.

"""A complete training loop against a hosted Future AGI environment.

Run this to see the whole RL loop end to end on the retail-returns environment: ask for a task,
drive the episode, collect a reward that means what a simulation verdict means, and keep only
the samples that were actually judged.

    export FI_API_KEY=...
    python examples/rl_reference_trainer.py --environment retail-returns --episodes 20

The policy here is a hand-written rule, not a model, so the loop is the thing you are reading
rather than anything clever. Swap `decide` for your model's action choice and the rest stands.

The one thing to copy carefully is how the reward is handled. A trial that could not be graded
returns no reward at all, and that is **not** a zero -- training on it as one teaches a model
that correct behaviour was wrong. The loop below counts those separately and drops them, which
is what `result.usable` is for.
"""

from __future__ import annotations

import argparse
import collections
from typing import Any

import fi.alk as alk


def decide(episode: Any) -> str:
    """One trajectory: read the order, read the policy, then refund or explain the refusal.

    Replace this with your model. It receives the episode and may call any published tool; the
    only contract is that it finishes having either acted or explained itself.
    """
    prompt = episode.prompt()
    order_id = next(
        (word.strip(".,") for word in prompt.split() if word.strip(".,").startswith("ORD-")),
        None,
    )
    if order_id is None:
        episode.say("Could you give me the order number?")
        return "asked_for_order"

    order = episode.call("look_up_order", order_id=order_id)
    if not order.ok or not (order.output or {}).get("found"):
        episode.say(f"I cannot find order {order_id}.")
        return "not_found"

    policy = episode.call("return_policy")
    window = (policy.output or {}).get("window_days", 30) if policy.ok else 30
    age = order.output["days_since_delivery"]

    if age > window:
        # A correct refusal writes nothing and must still earn a pass.
        episode.say(
            f"Order {order_id} was delivered {age} days ago, outside our {window}-day window,"
            " so I cannot refund it."
        )
        return "refused"

    refund = episode.call("refund", order_id=order_id)
    if not refund.ok:
        episode.say(f"I could not refund {order_id}.")
        return "refund_failed"
    episode.say(f"I have refunded order {order_id}.")
    return "refunded"


def train(environment: str, *, split: str, count: int, base_url: str | None) -> None:
    handles = alk.trainer.tasks(environment, split=split, base_url=base_url)
    if not handles:
        raise SystemExit(f"no tasks in split {split!r} of {environment!r}")

    rewards: list[float] = []
    dropped: collections.Counter[str] = collections.Counter()
    actions: collections.Counter[str] = collections.Counter()

    for step in range(count):
        handle = handles[step % len(handles)]
        try:
            with alk.trainer.episode(environment, task=handle, base_url=base_url) as episode:
                actions[decide(episode)] += 1
                result = episode.finish()
        except alk.trainer.ActionAmbiguous as exc:
            # A write may or may not have landed. Never guess a label for it.
            dropped["ambiguous_action"] += 1
            print(f"  episode {step + 1}: dropped, {exc}")
            continue

        if result.usable:
            rewards.append(result.reward or 0.0)
            print(f"  episode {step + 1}: reward {result.reward}")
        else:
            # Not a zero. Nobody judged this trial.
            dropped[result.grading_code or result.status] += 1
            print(f"  episode {step + 1}: no label ({result.grading_code or result.status})")

    print(f"\n{len(rewards)} of {count} episodes carried a training label")
    if rewards:
        print(f"mean reward: {sum(rewards) / len(rewards):.3f}")
    if dropped:
        print("dropped, by reason: " + ", ".join(f"{k}={v}" for k, v in sorted(dropped.items())))
    print("actions taken: " + ", ".join(f"{k}={v}" for k, v in sorted(actions.items())))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", default="retail-returns")
    parser.add_argument("--split", default="train")
    parser.add_argument("--episodes", type=int, default=10)
    parser.add_argument("--base-url", default=None, help="defaults to the configured API URL")
    args = parser.parse_args()
    train(
        args.environment,
        split=args.split,
        count=args.episodes,
        base_url=args.base_url,
    )


if __name__ == "__main__":
    main()

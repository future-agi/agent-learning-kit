"""Plays a scenario's scheduled world actors: each action, at its trigger, through the world's door.

It decides nothing and calls no model. Every action goes to the tool proxy under the actor's id and
this trial's token, so the proxy checks the actor's permissions and logs the attempt with what the
world made of it, whether or not the world was called.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Callable
from typing import Any
from urllib import error, request

from .actors import AGENT, Actor, ScenarioActor, ScheduledAction

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.25


def _send(url: str, headers: dict[str, str], arguments: dict[str, Any]) -> int:
    outgoing = request.Request(
        url,
        data=json.dumps(arguments).encode(),
        method="POST",
        headers={"content-type": "application/json", **headers},
    )
    try:
        with request.urlopen(outgoing, timeout=30) as response:
            return int(response.status)
    except error.HTTPError as exc:
        return exc.code


def _contacts(row: dict[str, Any], actor: Actor) -> bool:
    """Whether one of the agent's actions is addressed to one of this actor's identities."""
    said = json.dumps(row.get("arguments") or {}, default=str)
    return any(identity and f'"{identity}"' in said for identity in actor.identities.values())


class ActorDispatcher:
    def __init__(
        self,
        actors: dict[str, Actor],
        cast: list[ScenarioActor],
        *,
        trial_id: str,
        proxy_url: str,
        token: str,
        read_log: Callable[[], list[dict[str, Any]]],
        seed: str,
        clock: Callable[[], float] = time.monotonic,
        send: Callable[[str, dict[str, str], dict[str, Any]], int] = _send,
    ) -> None:
        self.proxy_url = proxy_url.rstrip("/")
        self.token = token
        self.trial_id = trial_id
        self.read_log = read_log
        self.clock = clock
        self.send = send
        self.pending: list[tuple[Actor, int, ScheduledAction]] = [
            (actors[entry.id], index, action)
            for entry in cast
            if entry.id in actors
            for index, action in enumerate(entry.actions)
        ]
        # A chance trigger is drawn once per trial, the same way on a rerun of it.
        self.draws = {
            (actor.id, index): random.Random(f"{seed}:{actor.id}:{index}").random()
            for actor, index, _ in self.pending
        }
        self.fired: list[dict[str, Any]] = []

    def _cause(
        self, actor: Actor, index: int, action: ScheduledAction, elapsed: float, log: list[dict[str, Any]]
    ) -> str | None:
        """Whether this action is due now: "" when due with no cause in the log, the causing action's id
        when an agent action caused it, None when not due yet."""
        trigger = action.trigger
        agent_rows = [row for row in log if (row.get("actor_id") or AGENT) == AGENT]
        if trigger.kind == "always":
            return ""
        if trigger.kind == "after_seconds":
            return "" if elapsed >= trigger.seconds else None
        if trigger.kind == "probability":
            return "" if self.draws[(actor.id, index)] < trigger.p else None
        if trigger.kind == "on_tool":
            # The log names an action by its last part, as the agent's tool calls always have been.
            calls = [row for row in agent_rows if row.get("name") == trigger.tool.rsplit(".", 1)[-1]]
            return str(calls[trigger.on_call - 1].get("action_id") or "") if len(calls) >= trigger.on_call else None
        if trigger.kind == "contacted":
            reached = next((row for row in agent_rows if _contacts(row, actor)), None)
            return None if reached is None else str(reached.get("action_id") or "")
        return None

    async def _fire(self, actor: Actor, index: int, action: ScheduledAction, cause: str) -> None:
        headers = {
            "X-ALK-Actor": actor.id,
            "X-ALK-Actor-Token": self.token,
            "Idempotency-Key": f"{self.trial_id}:{actor.id}:{index}",
        }
        if cause:
            headers["X-ALK-Parent-Event"] = cause
        url = f"{self.proxy_url}/{action.action.replace('.', '/')}"
        status = await asyncio.to_thread(self.send, url, headers, action.arguments)
        self.fired.append({"actor": actor.id, "action": action.action, "status": status, "parent": cause or None})

    async def run(self) -> None:
        """Fires every action once, when its trigger is met; runs until the trial is over and it is cancelled."""
        started = self.clock()
        while self.pending:
            try:
                log = await asyncio.to_thread(self.read_log)
            except Exception:  # noqa: BLE001 - an unreadable log this tick is retried next tick
                log = []
            elapsed = self.clock() - started
            for actor, index, action in list(self.pending):
                cause = self._cause(actor, index, action, elapsed, log)
                if cause is None:
                    continue
                self.pending.remove((actor, index, action))
                try:
                    await self._fire(actor, index, action, cause)
                except Exception as exc:  # noqa: BLE001 - one failed action never stops the rest
                    logger.warning("actor action %s by %s could not be sent: %s", action.action, actor.id, type(exc).__name__)
            if self.pending:
                await asyncio.sleep(POLL_SECONDS)

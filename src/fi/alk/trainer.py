"""Train a model against a hosted Future AGI environment.

PR 7. The customer side of the RL endpoints: a trainer asks for a task, drives an episode one
action at a time, and gets a reward that means what a Future AGI simulation verdict means.
We host the world, the simulated people and the grading; your model and your training loop
stay with you, and nothing about them crosses this boundary.

The shape is a context manager, because an episode holds a real world on our side and leaving
one open holds capacity you are paying attention to:

    import fi.alk as alk

    with alk.trainer.episode("retail-returns", split="train", index=0) as episode:
        order = episode.call("look_up_order", order_id="ORD-1041")
        policy = episode.call("return_policy")
        if order.output["days_since_delivery"] <= policy.output["window_days"]:
            episode.call("refund", order_id=order.output["order_id"])
        else:
            episode.say("That order is outside our returns window, so I cannot refund it.")
        result = episode.finish()

    if result.graded:
        train_on(result.reward)      # 1.0 or 0.0
    else:
        skip(result.grading_detail)  # the trial was never judged

**The one rule worth reading twice.** ``result.reward`` is ``None``, not ``0.0``, when a trial
could not be graded -- a judge outage, evidence that could not be read, a cancellation. Zero
means "your model did the wrong thing"; ``None`` means "nobody knows". Training on ``None`` as
if it were zero teaches a model that correct behaviour was wrong, so ``graded`` is the field to
branch on and ``reward`` is only meaningful once it is true.

Retries are deliberately narrow. An action carries an id, and re-sending the same id reconnects
to that action rather than performing it again -- which is what makes a dropped response safe
for a write like a refund. That window is short, so this client reconnects once and then reports
the ambiguity instead of guessing. It never retries a write on a fresh id, because a second
refund is a real second refund.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Protocol

import requests

from .config import current_config, get_api_key

# Our own field on a terminal result, carrying what the standard cannot express.
GRADING_FIELD = "futureagi_grading"
_DEFAULT_TIMEOUT = 60.0


class TrainerError(RuntimeError):
    """The service refused, or the episode cannot continue."""


class ActionAmbiguous(TrainerError):
    """A write may or may not have landed, and the service could not say which.

    Raised rather than retried. Re-sending a refund on a new id would be a second refund, and
    treating it as a failure would train against an action that may have succeeded, so the
    honest move is to surface it and let the caller drop the episode.
    """


@dataclass(frozen=True)
class ToolResult:
    """One action's outcome, as the participant sees it."""

    tool: str
    ok: bool
    output: Any = None
    error: str | None = None


@dataclass(frozen=True)
class EpisodeResult:
    """The terminal result: what the trial earned, and whether it was judged at all."""

    reward: float | None
    graded: bool
    status: str
    grading_code: str | None = None
    grading_detail: str | None = None

    @property
    def usable(self) -> bool:
        """Whether this trial carries a training label. Prefer this over ``reward is not None``."""
        return self.graded and self.reward is not None


class Transport(Protocol):
    """The HTTP calls this client makes. Injectable so a trainer can be tested offline."""

    def request(
        self, method: str, url: str, *, headers: Mapping[str, str], json_body: Any = None
    ) -> tuple[int, Any]: ...

    def stream(
        self, method: str, url: str, *, headers: Mapping[str, str], json_body: Any = None
    ) -> Iterator[tuple[str, Any]]: ...


class RequestsTransport:
    """The default transport, matching the rest of the SDK's use of ``requests``."""

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self._session = requests.Session()

    def request(
        self, method: str, url: str, *, headers: Mapping[str, str], json_body: Any = None
    ) -> tuple[int, Any]:
        response = self._session.request(
            method, url, headers=dict(headers), json=json_body, timeout=self.timeout
        )
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {"detail": response.text}

    def stream(
        self, method: str, url: str, *, headers: Mapping[str, str], json_body: Any = None
    ) -> Iterator[tuple[str, Any]]:
        """Server-sent events, yielded as (event name, data) as they arrive."""
        with self._session.request(
            method,
            url,
            headers={**headers, "Accept": "text/event-stream"},
            json=json_body,
            timeout=self.timeout,
            stream=True,
        ) as response:
            name = ""
            for raw in response.iter_lines(decode_unicode=True):
                line = (raw or "").strip()
                if not line:
                    continue
                if line.startswith("event:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    payload = line.split(":", 1)[1].strip()
                    try:
                        yield name, json.loads(payload)
                    except ValueError:
                        yield name, {"raw": payload}


class Episode:
    """One open episode. Obtained from ``episode()``, which closes it for you."""

    def __init__(
        self,
        *,
        base_url: str,
        environment: str,
        session_id: str,
        headers: Mapping[str, str],
        transport: Transport,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._environment = environment
        self._session_id = session_id
        self._headers = {**headers, "X-Session-ID": session_id}
        self._transport = transport
        self._finished = False
        self._last_action: str | None = None

    # --- reading the episode -----------------------------------------------------------

    def prompt(self) -> str:
        """The task, and anything the simulated person has said so far."""
        status, body = self._transport.request(
            "GET", f"{self._base}/{self._environment}/prompt", headers=self._headers
        )
        self._raise_for(status, body)
        blocks = body if isinstance(body, list) else []
        return "\n".join(
            str(block.get("text") or "") for block in blocks if block.get("type") == "text"
        )

    def tools(self) -> list[dict[str, Any]]:
        """The tools this task permits, with their argument names."""
        status, body = self._transport.request(
            "GET", f"{self._base}/{self._environment}/task_tools", headers=self._headers
        )
        self._raise_for(status, body)
        return list(body or [])

    # --- acting ------------------------------------------------------------------------

    def call(self, tool: str, *, _action_id: str | None = None, **arguments: Any) -> ToolResult:
        """Run one tool and return what it gave back.

        A failed action is a ``ToolResult`` with ``ok=False``, not an exception: a refusal is
        something a model should be able to see and respond to, the same way a person would.
        """
        if self._finished:
            raise TrainerError("this episode is finished; open a new one")
        action_id = _action_id or uuid.uuid4().hex
        self._last_action = action_id
        return self._run(tool, arguments, action_id)

    def say(self, message: str, *, tool: str = "say") -> ToolResult:
        """Speak to the simulated person. Dialogue is an action, so its order is preserved."""
        return self.call(tool, message=message)

    def reconnect(self) -> ToolResult | None:
        """Re-fetch the last action's result after a dropped connection.

        Returns ``None`` when nothing has been sent yet. The service keeps a completed result
        only briefly, so this is for an immediate reconnect, not for reading history later.
        """
        if self._last_action is None:
            return None
        return self._run(None, {}, self._last_action, reconnecting=True)

    def _run(
        self,
        tool: str | None,
        arguments: Mapping[str, Any],
        action_id: str,
        *,
        reconnecting: bool = False,
    ) -> ToolResult:
        body: dict[str, Any] = {"task_id": action_id}
        if tool is not None:
            body["name"] = tool
            body["input"] = dict(arguments)
        chunks: list[str] = []
        final: dict[str, Any] | None = None
        raised: str | None = None
        for name, data in self._transport.stream(
            "POST",
            f"{self._base}/{self._environment}/call",
            headers=self._headers,
            json_body=body,
        ):
            if name == "chunk":
                chunks.append(str(data.get("data") or ""))
            elif name == "end":
                final = data
            elif name == "error":
                raised = str(data.get("error") or "the action failed")

        if raised is not None:
            # The service could not say whether the action landed. Reconnect once, then stop.
            if reconnecting:
                raise ActionAmbiguous(raised)
            recovered = self._run(None, {}, action_id, reconnecting=True)
            return recovered
        if final is None:
            raise ActionAmbiguous(
                f"the stream for action {action_id} ended without a result"
            )
        if chunks and final.get("chunked"):
            try:
                final = json.loads("".join(chunks))
            except ValueError as exc:
                raise TrainerError(f"the chunked result could not be read: {exc}") from exc
        return ToolResult(
            tool=tool or "(reconnect)",
            ok=bool(final.get("ok")),
            output=final.get("output"),
            error=None if final.get("ok") else str(final.get("error") or "refused"),
        )

    # --- finishing ---------------------------------------------------------------------

    def finish(self) -> EpisodeResult:
        """Close the episode and read its reward. Safe to call once; the context manager will."""
        if self._finished:
            raise TrainerError("this episode is already finished")
        status, body = self._transport.request(
            "POST", f"{self._base}/delete", headers=self._headers
        )
        self._finished = True
        self._raise_for(status, body)
        grading = (body or {}).get(GRADING_FIELD) or {}
        graded = bool(grading.get("graded"))
        reward = body.get("reward") if isinstance(body, Mapping) else None
        return EpisodeResult(
            # Guard rather than trust: a null reward with graded=True would otherwise read as a
            # usable label, which is the one mistake this type exists to prevent.
            reward=float(reward) if graded and reward is not None else None,
            graded=graded,
            status=str(grading.get("status") or "unknown"),
            grading_code=grading.get("code"),
            grading_detail=grading.get("detail"),
        )

    def _close_quietly(self) -> None:
        """Release the world on the way out of a failed block, without masking the error."""
        if self._finished:
            return
        try:
            self.finish()
        except Exception:  # noqa: BLE001 - the original failure matters more than this one
            self._finished = True

    @staticmethod
    def _raise_for(status: int, body: Any) -> None:
        if status >= 400:
            detail = (body or {}).get("detail") if isinstance(body, Mapping) else body
            if status == 410:
                raise TrainerError(f"this episode was already closed ({detail})")
            raise TrainerError(f"the service refused with {status}: {detail}")


# --- opening an episode ----------------------------------------------------------------


def _headers(api_key: str | None) -> dict[str, str]:
    key = api_key or get_api_key(required=True)
    return {"Content-Type": "application/json", "X-Api-Key": str(key)}


def tasks(
    environment: str,
    *,
    split: str = "train",
    base_url: str | None = None,
    api_key: str | None = None,
    transport: Transport | None = None,
) -> list[str]:
    """The task handles in one split. Handles are opaque: the task resolves on our side."""
    base = (base_url or current_config().api_url or "").rstrip("/")
    client = transport or RequestsTransport()
    status, body = client.request(
        "POST",
        f"{base}/{environment}/tasks",
        headers=_headers(api_key),
        json_body={"split": split},
    )
    Episode._raise_for(status, body)
    return list((body or {}).get("tasks") or [])


@contextmanager
def episode(
    environment: str,
    *,
    split: str = "train",
    index: int | None = None,
    task: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    transport: Transport | None = None,
) -> Iterator[Episode]:
    """Open one episode on a task, and close it however the block ends.

    Give either ``index`` within ``split``, or an opaque ``task`` handle from ``tasks()``.
    Closing is not optional: an open episode holds a world on our side, so the exit path runs
    even when the block raises.
    """
    if (index is None) == (task is None):
        raise TrainerError("give exactly one of index= or task=")

    base = (base_url or current_config().api_url or "").rstrip("/")
    client = transport or RequestsTransport()
    headers = _headers(api_key)

    status, body = client.request("POST", f"{base}/create_session", headers=headers)
    Episode._raise_for(status, body)
    session_id = str((body or {}).get("sid") or "")
    if not session_id:
        raise TrainerError("the service did not return a session id")

    opening = {"env_name": environment}
    if task is not None:
        opening["task_spec"] = task
    else:
        opening["split"] = split
        opening["index"] = index
    status, body = client.request(
        "POST", f"{base}/create", headers={**headers, "X-Session-ID": session_id}, json_body=opening
    )
    Episode._raise_for(status, body)

    open_episode = Episode(
        base_url=base,
        environment=environment,
        session_id=session_id,
        headers=headers,
        transport=client,
    )
    try:
        yield open_episode
    finally:
        open_episode._close_quietly()

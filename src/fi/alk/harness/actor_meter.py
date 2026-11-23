"""What actors' model calls cost in one trial, counted into the call's simulator usage like the caller's own."""

from __future__ import annotations

import threading
from typing import Any


class ActorMeter:
    """Every model call an actor made this trial: who, on which model, tokens in and out, cost."""

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def add(self, actor_id: str, model: str, turn: Any) -> None:
        """One call, from a stage turn (or anything with `tokens_in`, `tokens_out`, `cost_usd`)."""
        with self._lock:
            self._rows.append(
                {
                    "actor": actor_id,
                    "model": model,
                    "tokens_in": int(getattr(turn, "tokens_in", 0) or 0),
                    "tokens_out": int(getattr(turn, "tokens_out", 0) or 0),
                    "cost_usd": float(getattr(turn, "cost_usd", 0.0) or 0.0),
                }
            )

    def rows(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(one) for one in self._rows]

    def totals(self) -> tuple[int, int, float]:
        rows = self.rows()
        return (
            sum(one["tokens_in"] for one in rows),
            sum(one["tokens_out"] for one in rows),
            sum(one["cost_usd"] for one in rows),
        )

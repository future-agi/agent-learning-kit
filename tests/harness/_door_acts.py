"""An actor's actions through the world's door, with the door answered by a test's `send`."""

from __future__ import annotations

from types import SimpleNamespace

from fi.alk.harness.actor_dispatch import DoorActs


class RecordingDoor:
    """Stands where the world's door stands: each call reaches `send(url, headers, arguments)` as the service
    would see it, and the answer is read back the way the door reads one."""

    def __init__(self, send, base: str = "http://p") -> None:
        self.send = send
        self.base = base.rstrip("/")

    def enter(self, call):
        headers = {"X-ALK-Actor": call.actor, "Idempotency-Key": call.key}
        if call.parent:
            headers["X-ALK-Parent-Event"] = call.parent
        if call.form == "record":
            headers["X-ALK-Record"] = "1"
        if call.identity:
            headers["X-ALK-Actor-Identity"] = call.identity
        sent = self.send(f"{self.base}/{call.action.replace('.', '/')}", headers, call.arguments)
        if isinstance(sent, int):
            sent = {"status": sent, "result": None}
        status = int(sent.get("status") or 0)
        outcome = "committed" if 0 < status < 400 else "refused" if status < 500 else "faulted"
        return SimpleNamespace(status=status, outcome=outcome, result=sent.get("result"))


def door_acts(send=None, base: str = "http://p") -> DoorActs:
    return DoorActs(RecordingDoor(send or (lambda url, headers, arguments: 200), base))

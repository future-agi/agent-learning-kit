"""A Slack post retried after a lost reply returns the original message (plan §3.3), in every
vertical whose desk posts to Slack: the same sender, destination, thread, text and request key
replay without posting again or needing a fresh approval. The same key for anything else is a
conflict, and the used approval cannot cover a second post.
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest

from fi.alk import environments
from fi.alk.environments import designed
from fi.alk.harness.folder import read_folder
from fi.alk.harness.prove import _run, play_reference_step
from fi.alk.harness.releases import WORLD

VERTICALS = ("hr", "internal_it", "saas_subscriptions", "sales_intake", "internal_knowledge")


def _posted(here: Path, world_root: Path):
    """A world in which a reference post just went through, the post, and its first result: the
    first scenario whose reference posts successfully (a post without a request_key gets one,
    since a retry is what a key is for)."""
    for folder in sorted(p for p in (here / "suite" / "scenarios").iterdir() if p.is_dir()):
        scenario = read_folder(here / "suite", folder.name)
        if not any(s.tool == "slack_send_message" for s in scenario.solution):
            continue
        world, *_ = _run(scenario, world_root, with_solution=False)
        for step in scenario.solution:
            if step.tool == "slack_send_message" and not step.arguments.get("request_key"):
                step.arguments["request_key"] = f"RETRY-{folder.name}"
            played = play_reference_step(world, step)
            if step.tool == "slack_send_message" and played.ok:
                return world, step, played
        world.close()
    raise AssertionError(f"no reference in {here.name} posts to Slack successfully")


@pytest.mark.parametrize("vertical", VERTICALS)
def test_an_identical_retry_returns_the_original_post(vertical: str, tmp_path: Path) -> None:
    here = Path(environments.__file__).parent / vertical
    designed.build_environment(here, tmp_path / "environment")
    shutil.copytree(tmp_path / "environment" / WORLD, tmp_path / WORLD)
    world, post, first = _posted(here, tmp_path / WORLD)
    try:
        posted = len(world.state()["slack_messages"])
        again = play_reference_step(world, post)
        assert again.ok and again.result["status"] == "idempotent_replay", again.error
        assert again.result["message_id"] == first.result["message_id"]
        assert len(world.state()["slack_messages"]) == posted

        changed = copy.deepcopy(post)
        changed.arguments["message"] = post.arguments["message"] + " (edited)"
        conflict = play_reference_step(world, changed)
        assert not conflict.ok and "already used for a different message" in conflict.error

        second = copy.deepcopy(post)
        second.arguments["request_key"] = post.arguments["request_key"] + "-second"
        reused = play_reference_step(world, second)
        assert not reused.ok and "already used" in reused.error
        assert len(world.state()["slack_messages"]) == posted
    finally:
        world.close()

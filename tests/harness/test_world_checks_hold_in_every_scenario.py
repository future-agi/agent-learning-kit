"""Every Wave A scenario starts and ends in a consistent world (plan §3.2): the vertical's own world
checks hold after the scenario's setup and again after its reference solution. A sub-goal that
passes says nothing about the rest of the world, so a fixture with a phantom balance, a
double-booked calendar or an order without an invoice would otherwise go unnoticed.

A scenario whose point is an inconsistent record (an email from a look-alike domain, a policy with
no approved version) is listed in `INTENDED` with the check it breaks and why. Nothing else may
break one, and an entry that no longer breaks its check fails too, so the list stays exact.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from fi.alk import environments
from fi.alk.environments import designed
from fi.alk.harness.checks import run_world_check
from fi.alk.harness.folder import read_folder
from fi.alk.harness.prove import _run
from fi.alk.harness.releases import WORLD

VERTICALS = (
    "retail",
    "hr",
    "internal_it",
    "saas_subscriptions",
    "clinic_scheduling",
    "service_scheduling",
    "sales_intake",
    "internal_knowledge",
)

INTENDED = {
    ("retail", "unverified_email_claim_exception_rejected", "check_mailbox_retail_consistency"):
        "the customer's email cites a return R77 that does not exist; the claim is what the desk must not act on",
    ("saas_subscriptions", "cancellation_from_a_look_alike_sender", "check_mailbox_billing_consistency"):
        "the cancellation comes from a look-alike domain that is no tenant's user; spotting that is the scenario",
    ("internal_knowledge", "approved_but_not_yet_in_effect", "check_one_effective_version"):
        "a second approved version takes effect later; answering from the one in effect today is the scenario",
    ("internal_knowledge", "only_a_draft_exists", "check_one_effective_version"):
        "the policy has only a draft; saying there is no approved policy yet is the scenario",
}


def _breaks(vertical: str, root: Path) -> set[tuple[str, str, str]]:
    here = Path(environments.__file__).parent / vertical
    checks = json.loads((here / "environment.json").read_text())["world_checks"]
    designed.build_environment(here, root / "environment")
    shutil.copytree(root / "environment" / WORLD, root / WORLD)
    found = set()
    for folder in sorted(p for p in (here / "suite" / "scenarios").iterdir() if p.is_dir()):
        scenario = read_folder(here / "suite", folder.name)
        for with_solution in (False, True):
            world, *_ = _run(scenario, root / WORLD, with_solution=with_solution)
            try:
                found |= {
                    (vertical, folder.name, name)
                    for name, source in sorted(checks.items())
                    if not run_world_check(source, world, name=name).held
                }
            finally:
                world.close()
    return found


@pytest.mark.parametrize("vertical", VERTICALS)
def test_world_checks_hold_after_setup_and_reference(vertical: str, tmp_path: Path) -> None:
    intended = {key for key in INTENDED if key[0] == vertical}
    assert _breaks(vertical, tmp_path) == intended

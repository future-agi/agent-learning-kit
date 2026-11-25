"""Whether a build has the environment its agent needs, checked at the build's start.

An agent read as driving a web browser is stored as a chat agent that needs a browser
(``contract.how_it_runs``). It runs only in an environment that gives it one, and the job says
which sites that browser may open in ``metadata.browser_allowed_sites``. A browser agent with no
sites is refused here, before anything is installed: refusing it at the reading is impossible, as a
reading knows no job.

The list is read strictly. Absent means the environment has no browser. Present, it must be a
non-empty list of host names, each one the run's egress allows where the job also controls
egress: a list that is wrong is the platform's fault, shown at once rather than as pages that never
load.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contract import AgentContract, how_it_runs
from .egress_proxy import LOOPBACK, host_allowed
from .job import FailureDomain, HarnessFailure, HarnessStage

BROWSER_ALLOWED_SITES = "browser_allowed_sites"
BROWSER_ENVIRONMENT_REQUIRED = "browser_environment_required"
BROWSER_SITES_INVALID = "browser_allowed_sites_invalid"
BROWSER_SITE_NOT_IN_EGRESS = "browser_site_not_in_egress"

# A host name: dot-separated labels of letters, digits and inner hyphens, with an optional `*.`
# for every name under it, as the egress list writes it. No scheme, port, path or address.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
# ASCII only: with IGNORECASE alone, [a-z] also takes letters a browser reads as others ("ſ" as "s").
_HOST = re.compile(rf"(?:\*\.)?{_LABEL}(?:\.{_LABEL})*", re.IGNORECASE | re.ASCII)
# A browser reads a host as an IPv4 address when its last label is a number (decimal, octal or
# `0x` hex), as the URL standard does: `0x7f000001` is 127.0.0.1. Such a host is never a site.
_NUMBER_LABEL = re.compile(r"[0-9]+|0x[0-9a-f]*", re.IGNORECASE | re.ASCII)


class SitesProblem(ValueError):
    """The job's site list can't be used; ``failure`` says why, for the platform."""

    def __init__(self, failure: HarnessFailure) -> None:
        super().__init__(failure.code)
        self.failure = failure


def _failure(code: str, message: str, **details: Any) -> HarnessFailure:
    return HarnessFailure(
        domain=FailureDomain.PLATFORM_SYNC,
        stage=HarnessStage.QUEUED,
        code=code,
        message=message,
        retryable=False,
        details=details,
    )


def _is_host(site: str) -> bool:
    name = site.removeprefix("*.").lower()
    return (
        len(site) <= 253
        and _HOST.fullmatch(site) is not None
        # An address is not a site, and loopback holds the world itself.
        and _NUMBER_LABEL.fullmatch(name.rsplit(".", 1)[-1]) is None
        and name not in LOOPBACK
        and not name.endswith(".localhost")
    )


# The sites a message names before it counts the rest: the platform reads a message of at most
# 2000 characters, and the whole list is in the failure's details.
_NAMED_IN_A_MESSAGE = 5


def _some(sites: list[str]) -> str:
    named = ", ".join(sites[:_NAMED_IN_A_MESSAGE])
    rest = len(sites) - _NAMED_IN_A_MESSAGE
    return f"{named}, and {rest} more" if rest > 0 else named


def allowed_sites(metadata: Mapping[str, Any] | None) -> tuple[str, ...] | None:
    """The sites the job's browser may open, or None when the job gives it no browser.

    Raises ``SitesProblem`` for a list that is present and wrong.
    """
    if not isinstance(metadata, Mapping) or BROWSER_ALLOWED_SITES not in metadata:
        return None
    raw = metadata[BROWSER_ALLOWED_SITES]
    if not isinstance(raw, list) or not raw:
        raise SitesProblem(
            _failure(
                BROWSER_SITES_INVALID,
                "The job's browser sites must be a list of at least one host name.",
                reason="empty" if isinstance(raw, list) else "not_a_list",
            )
        )
    wrong = [index for index, site in enumerate(raw) if not isinstance(site, str) or not _is_host(site)]
    if wrong:
        # Positions only: a value that isn't a host name may be anything, a URL with a password
        # among them.
        raise SitesProblem(
            _failure(
                BROWSER_SITES_INVALID,
                "The job's browser sites must be host names, with no scheme, port, path or address.",
                reason="not_a_host_name",
                positions=wrong,
            )
        )
    sites = tuple(dict.fromkeys(site.lower() for site in raw))
    egress = metadata.get("egress_allowed_hosts")
    if isinstance(egress, list) and all(isinstance(one, str) for one in egress):
        outside = [site for site in sites if not host_allowed(site.removeprefix("*."), egress)]
        if outside:
            raise SitesProblem(
                _failure(
                    BROWSER_SITE_NOT_IN_EGRESS,
                    "The run's egress doesn't allow these browser sites: " + _some(outside) + ".",
                    sites=outside,
                )
            )
    return sites


def job_failure(metadata: Mapping[str, Any] | None) -> HarnessFailure | None:
    """What is wrong with the job's site list on its own, before any contract is known."""
    try:
        allowed_sites(metadata)
    except SitesProblem as problem:
        return problem.failure
    return None


def environment_failure(
    contract: AgentContract, metadata: Mapping[str, Any] | None
) -> HarnessFailure | None:
    """The refusal of an agent the job gives no environment it needs; None when it has one."""
    if "browser" not in how_it_runs(contract).needs:
        return None
    try:
        sites = allowed_sites(metadata)
    except SitesProblem as problem:
        return problem.failure
    if sites:
        return None
    return HarnessFailure(
        domain=FailureDomain.ENVIRONMENT,
        stage=HarnessStage.UNDERSTANDING_AGENT,
        code=BROWSER_ENVIRONMENT_REQUIRED,
        message=(
            "This agent drives a web browser, so it runs only in an environment that gives it a "
            "browser and the sites it may open. This job names no sites."
        ),
        retryable=False,
        details={"needs": ["browser"]},
    )


def saved_contract_failure(folder: Path, metadata: Mapping[str, Any] | None) -> HarnessFailure | None:
    """The refusal of the contract saved in ``folder``, when its agent needs an environment the job
    doesn't give. None when there is none to refuse, no contract yet, or one that can't be read:
    that is for the steps that read it."""
    from .understand import load

    try:
        contract = load(folder)
    except (OSError, ValueError):
        return None
    return None if contract is None else environment_failure(contract, metadata)


def refuse_saved_contract(folder: Path, metadata: Mapping[str, Any] | None) -> str | None:
    """``saved_contract_failure``, written to ``folder`` for the build; the refusal's code, or None."""
    from .failure_file import write_failure_file

    failure = saved_contract_failure(folder, metadata)
    if failure is None:
        return None
    write_failure_file(failure, folder)
    return failure.code

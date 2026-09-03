"""Where a suite lives, and the only place allowed to change it.

A suite exists in three forms at once: the scenarios a session holds in memory, the journal each
writer appends to as it proves one, and the folders on disk that everything downstream reads. They
drift apart the moment any of them is written without the others in view, and every way this stage
has lost proved work came from that drift: a credit ledger that read folders while writers
journalled, a watchdog that killed a productive run for the same reason, and a second save that
deleted what the first had written.

Reconciling them is this module's job and nobody else's. ``save_suite`` is the one way a suite
reaches disk, because writing prunes whatever it was not handed, and a source left out of the fold
is a source deleted.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ..model.catalogue import Catalogue, load_catalogue
from .folder import SCENARIOS, read_all, write_folder, write_index
from ..model.scenario import Scenario

def write_scenarios(
    scenarios: list[Scenario], destination: Path, catalogue: Catalogue | None = None
) -> Path:
    """Write every scenario out as its own folder, and regenerate the index over them."""
    catalogue = catalogue if catalogue is not None else load_catalogue(destination)
    for one in scenarios:
        write_folder(one, catalogue, destination)
    _forget_dropped(scenarios, destination)
    return write_index(scenarios, destination)


def _forget_dropped(scenarios: list[Scenario], destination: Path) -> None:
    """Remove the folders of scenarios that are no longer in the suite.

    The folders are the truth, and they are what gets read back. Writing the survivors without
    taking the others away means a dropped scenario returns on the next load, still failing, and
    dropping it appears to do nothing at all.
    """
    import shutil

    root = Path(destination) / SCENARIOS
    if not root.exists():
        return
    keeping = {one.name for one in scenarios}
    for folder in root.iterdir():
        if folder.is_dir() and folder.name not in keeping:
            shutil.rmtree(folder)


JOURNAL = "written.jsonl"


def record_written(scenarios: list[Scenario], destination: Path) -> None:
    """Append what a writer proved, so a run that dies still has it.

    Under delegation the writers hold their work in memory and the stage saves once at the end,
    because saving rewrites the index and deletes folders it does not know about, so two writers
    saving at once would delete each other. That is the right call for the index and the wrong
    one for durability: a suite of five hundred is hours of proving, and until the final save
    none of it is anywhere but RAM.

    This is the cheap half of the fix. It is append-only and touches neither the folders nor the
    index, so it cannot race the writers; it exists to be replayed by ``journalled`` if the final
    save never happens.
    """
    if not scenarios:
        return
    path = Path(destination) / JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for one in scenarios:
            handle.write(one.model_dump_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def journalled(destination: Path) -> list[Scenario]:
    """What the journal holds, for a run picking up after one that died.

    A killed process can leave a half-written final line, so an unreadable line is dropped rather
    than raising: the point of the journal is to save what survived, and refusing to read it
    because of the one record that did not would throw away the rest.
    """
    path = Path(destination) / JOURNAL
    if not path.is_file():
        return []
    kept: list[Scenario] = []
    # Keyed by name, because a retried slice journals what it had already proved a second time and
    # the caller renames folder-name collisions rather than dropping them, so a repeat would come
    # back as a `-2` folder holding the same test.
    taken: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            one = Scenario.model_validate_json(line)
        except Exception:  # noqa: BLE001 - a torn last line is expected, not exceptional
            continue
        if one.name in taken:
            continue
        taken.add(one.name)
        kept.append(one)
    return kept


def forget_journal(destination: Path) -> None:
    """Drop the journal once the suite is on disk, so the next run starts from nothing."""
    path = Path(destination) / JOURNAL
    if path.is_file():
        path.unlink()


def load_scenarios(destination: Path) -> list[Scenario]:
    """Every scenario on disk, read from its folder.

    The folders are the truth. The index beside them is regenerated from these, so it can
    describe them but never contradict them.
    """
    return read_all(destination)


def save_suite(
    kept: list[Scenario], destination: Path, catalogue: Catalogue | None = None
) -> Path:
    """Everything this run has proved, from wherever it currently lives, written out once.

    Folded in order of authority: what the caller holds, then the journal, then what is already on
    disk. The journal is dropped afterwards, because from then on the folders are the truth and a
    stale journal would re-import them on the next run.
    """
    held = {one.name for one in kept}
    for one in (*journalled(destination), *load_scenarios(destination)):
        if one.name in held:
            continue
        held.add(one.name)
        kept.append(one)
    written = write_scenarios(kept, destination, catalogue)
    forget_journal(destination)
    return written

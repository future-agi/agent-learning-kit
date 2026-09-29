"""Execute one simulation with the environment supplied at process creation."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .isolated_process import run_worker


async def main() -> None:
    from fi.simulate.runtime import SimulationSpec

    from .call_runner import _default_place_call

    spec = SimulationSpec.model_validate_json(sys.stdin.buffer.read())
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s %(levelname)s voice_run={spec.run_id} pid=%(process)d %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger(__name__).info("voice worker started")
    report = await _default_place_call(spec)
    output = Path(sys.argv[1])
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(report.model_dump_json())
    logging.getLogger(__name__).info("voice worker completed")


if __name__ == "__main__":
    run_worker(main)

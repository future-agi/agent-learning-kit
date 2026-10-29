"""Build and seal banking (Banking vertical; phase-1 sheet coverage V01, with the Gmail tool set of M02).

Designed by the environment builder; see `fi.alk.environments.designed`.

    python -m fi.alk.environments.banking.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

"""Build and seal internal_it (Internal IT vertical; phase-1 sheet coverage V20).

Designed by the environment builder from phase-1 sheet task V20; see
`fi.alk.environments.designed`.

    python -m fi.alk.environments.internal_it.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

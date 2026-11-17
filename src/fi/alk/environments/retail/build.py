"""Build and seal retail (Retail vertical; phase-1 sheet coverage V15).

Designed by the environment builder from phase-1 sheet task V15; see
`fi.alk.environments.designed`.

    python -m fi.alk.environments.retail.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

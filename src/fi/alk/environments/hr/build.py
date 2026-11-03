"""Build and seal hr (HR vertical; phase-1 sheet coverage V13).

Designed by the environment builder from phase-1 sheet task V13; see
`fi.alk.environments.designed`.

    python -m fi.alk.environments.hr.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

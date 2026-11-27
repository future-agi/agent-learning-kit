"""Build and seal service_scheduling (Service scheduling vertical; phase-1 sheet coverage V12).

Written for Wave A (plan §4.4); see `fi.alk.environments.designed`.

    python -m fi.alk.environments.service_scheduling.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

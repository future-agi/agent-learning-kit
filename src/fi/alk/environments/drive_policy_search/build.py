"""Build and seal drive_policy_search (M04, Google Drive: Google Drive policy search with version-aware citations).

Designed by the environment builder from phase-1 sheet task M04; see
`fi.alk.environments.designed`.

    python -m fi.alk.environments.drive_policy_search.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

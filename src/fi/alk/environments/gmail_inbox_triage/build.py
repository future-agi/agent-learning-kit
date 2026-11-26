"""Build and seal gmail_inbox_triage (M02, Gmail: Gmail inbox triage and grounded reply drafts).

Designed by the environment builder from phase-1 sheet task M02; see
`fi.alk.environments.designed`.

    python -m fi.alk.environments.gmail_inbox_triage.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

"""Build and seal slack_channel_digest (M01, Slack: Slack channel catch-up and approved status digest).

Designed by the environment builder from phase-1 sheet task M01; see
`fi.alk.environments.designed`.

    python -m fi.alk.environments.slack_channel_digest.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

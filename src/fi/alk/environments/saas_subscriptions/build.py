"""Build and seal saas_subscriptions (SaaS subscriptions vertical; phase-1 sheet coverage V16).

Designed by the environment builder from phase-1 sheet task V16; see
`fi.alk.environments.designed`.

    python -m fi.alk.environments.saas_subscriptions.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

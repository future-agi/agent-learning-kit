"""Build and seal subscription_billing_desk (V16, SaaS subscriptions: Explain a subscription bill and schedule a plan change).

Designed by the environment builder from phase-1 sheet task V16; see
`fi.alk.environments.designed`.

    python -m fi.alk.environments.subscription_billing_desk.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

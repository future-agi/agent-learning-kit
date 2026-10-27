"""Build and seal retail_returns_desk (V15, Retail and e-commerce: Resolve an order issue and create an eligible return).

Designed by the environment builder from phase-1 sheet task V15; see
`fi.alk.environments.designed`.

    python -m fi.alk.environments.retail_returns_desk.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

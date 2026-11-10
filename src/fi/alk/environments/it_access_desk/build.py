"""Build and seal it_access_desk (V20, Internal IT service: Handle an application-access request with least privilege).

Designed by the environment builder from phase-1 sheet task V20; see
`fi.alk.environments.designed`.

    python -m fi.alk.environments.it_access_desk.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

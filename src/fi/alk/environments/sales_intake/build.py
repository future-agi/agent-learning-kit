"""Build and seal sales_intake (Sales intake vertical; phase-1 sheet coverage V17).

Written for Wave A increment 9 from phase-1 sheet task V17; see
`fi.alk.environments.designed`.

    python -m fi.alk.environments.sales_intake.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

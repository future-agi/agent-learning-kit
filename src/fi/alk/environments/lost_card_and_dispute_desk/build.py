"""Build and seal lost_card_and_dispute_desk (V01, Banking: Lock a lost card and open a transaction dispute).

Designed by the environment builder from phase-1 sheet task V01; see
`fi.alk.environments.designed`.

    python -m fi.alk.environments.lost_card_and_dispute_desk.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

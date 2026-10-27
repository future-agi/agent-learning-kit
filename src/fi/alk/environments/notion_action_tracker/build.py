"""Build and seal notion_action_tracker (M05, Notion: Notion meeting notes to an approved action tracker).

Designed by the environment builder from phase-1 sheet task M05; see
`fi.alk.environments.designed`.

    python -m fi.alk.environments.notion_action_tracker.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

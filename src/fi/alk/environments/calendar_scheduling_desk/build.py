"""Build and seal calendar_scheduling_desk (M03, Google Calendar: Google Calendar meeting scheduling and rescheduling).

Designed by the environment builder from phase-1 sheet task M03; see
`fi.alk.environments.designed`.

    python -m fi.alk.environments.calendar_scheduling_desk.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

"""Build and seal hr_leave_desk (V13, Human resources: Answer leave policy questions and submit a leave request).

Designed by the environment builder from phase-1 sheet task V13; see
`fi.alk.environments.designed`.

    python -m fi.alk.environments.hr_leave_desk.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

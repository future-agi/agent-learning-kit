"""Build and seal clinic_scheduling (Clinic scheduling vertical; phase-1 sheet coverage V02).

    python -m fi.alk.environments.clinic_scheduling.build --out DIR [--postgres-dsn DSN]
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

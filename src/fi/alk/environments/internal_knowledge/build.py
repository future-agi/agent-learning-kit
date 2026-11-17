"""Build and seal internal_knowledge (Internal knowledge vertical; phase-1 sheet coverage V11).

Written for Wave A increment 10 (`internal-docs` plan §4.3); see `fi.alk.environments.designed`.

    python -m fi.alk.environments.internal_knowledge.build --out DIR
"""

from pathlib import Path

from fi.alk.environments.designed import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent))

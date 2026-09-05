"""Scenario generation: planning a suite, writing it, proving it, and keeping it.

``BUNDLED`` is where the data this stage ships with lives: the persona vocabulary the platform
will accept, and the axis sets a grid is derived from. Modules reach it through this name rather
than counting parent directories from their own ``__file__``, which silently broke every time one
of them moved.
"""

from pathlib import Path

BUNDLED = Path(__file__).parent / "data"

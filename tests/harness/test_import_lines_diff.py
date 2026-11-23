"""The line search against ``ast``: every import ``ast`` sees is found, or the search capped.

A small seeded run; ``python -m tests.harness.import_lines_diff --seed N --files M`` runs a larger
one, or another seed, and ``--real DIR`` adds the ``.py`` files of a folder.
"""

from __future__ import annotations

import pytest
from import_lines_diff import compare, generate, truth


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_no_import_ast_sees_is_missed_by_the_line_search(seed):
    files = generate(seed, 100)
    # The files are legal and mixed, or a run with no misses would say nothing.
    assert len(files) == 100
    joined = "\n".join(files)
    for needle in ("\r\n", "\t", "'''", "lambda", "match (", "from.", "@dec", "):"):
        assert needle in joined, needle
    assert sum(bool(truth(text)) for text in files) > 70
    misses = []
    for text in files:
        missed, _ = compare(text)
        if missed:
            misses.append((sorted(missed)[:3], text[:200]))
    assert misses == []

"""Telling two scenarios apart when they share no words.

The lexical check has a limit that no better string metric fixes: "caller cannot find their
booking" and "the booking cannot be found by the caller" share two content words out of four.
Embeddings settle that pair at 0.95. They do not settle everything, and the tests say where the
line is rather than implying the problem is solved.

Nothing here reaches the network. The embedding call is the seam, and it is stubbed.
"""

from __future__ import annotations

import pytest

from fi.alk.harness import semantic


@pytest.fixture()
def stub(monkeypatch):
    """Vectors chosen so the pairs land where the real model put them, measured."""
    known = {
        "caller cannot find their booking": [1.0, 0.0, 0.0],
        "the booking cannot be found by the caller": [0.96, 0.28, 0.0],
        "surge boundary fare confusion": [0.0, 1.0, 0.0],
        "same street name in two cities": [0.0, 0.0, 1.0],
    }
    monkeypatch.setattr(
        semantic, "vectors", lambda lines: [known.get(one, [0.0, 0.0, 0.0]) for one in lines]
    )


class TestWhatItCatches:
    def test_a_rewording_with_almost_no_shared_words_is_caught(self, stub):
        found = semantic.duplicates(
            [
                ("A1", "caller cannot find their booking"),
                ("A2", "the booking cannot be found by the caller"),
            ]
        )
        assert [(one.one, one.two) for one in found] == [("A1", "A2")]

    def test_genuinely_different_angles_are_left_alone(self, stub):
        found = semantic.duplicates(
            [
                ("A1", "caller cannot find their booking"),
                ("A3", "surge boundary fare confusion"),
                ("A4", "same street name in two cities"),
            ]
        )
        assert found == []

    def test_two_cells_may_share_a_situation(self, stub):
        """Same as the lexical pass: comparing across cells pushes cells apart artificially."""
        found = semantic.duplicates(
            [
                ("A1", "caller cannot find their booking"),
                ("A2", "the booking cannot be found by the caller"),
            ],
            within={"A1": "retrieve-ride", "A2": "cancel-ride"},
        )
        assert found == []


class TestItIsOptional:
    def test_no_credentials_means_no_answer_rather_than_no_run(self, monkeypatch):
        """A duplicate check is worth having and never worth stopping a run over."""
        monkeypatch.setattr(semantic, "vectors", lambda _lines: None)
        assert semantic.duplicates([("A1", "one"), ("A2", "two")]) is None
        assert semantic.spread([("A1", "one"), ("A2", "two"), ("A3", "three")]) is None

    def test_a_client_that_cannot_be_built_is_not_an_error(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        assert semantic.vectors(["anything"]) is None


class TestSpread:
    def test_a_varied_set_scores_lower_than_a_repetitive_one(self, stub):
        varied = semantic.spread(
            [
                ("A1", "caller cannot find their booking"),
                ("A3", "surge boundary fare confusion"),
                ("A4", "same street name in two cities"),
            ]
        )
        same = semantic.spread(
            [
                ("A1", "caller cannot find their booking"),
                ("A2", "the booking cannot be found by the caller"),
                ("A2b", "the booking cannot be found by the caller"),
            ]
        )
        assert varied and same
        assert varied[0] < same[0]

    def test_every_item_gets_a_place_to_plot(self, stub):
        found = semantic.spread(
            [
                ("A1", "caller cannot find their booking"),
                ("A3", "surge boundary fare confusion"),
                ("A4", "same street name in two cities"),
            ]
        )
        assert found and len(found[1]) == 3
        assert all(len(one) == 3 for one in found[1])

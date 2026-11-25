import typing

from fi.alk.harness.prove import not_run
from fi.alk.harness.world.runtime import Call


def test_a_recorded_unrun_call_is_neither_through_nor_refused_and_the_call_type_allows_that_state():
    call = not_run("send_receipt", {"order": "A1"})

    assert (call.ok, call.refused, call.refusal_code, call.error) == (None, None, None, None)
    assert call.arguments == {"order": "A1"} and call.result is None
    hints = typing.get_type_hints(Call)
    for name in ("ok", "refused", "refusal_code", "error"):
        assert type(None) in typing.get_args(hints[name]), name

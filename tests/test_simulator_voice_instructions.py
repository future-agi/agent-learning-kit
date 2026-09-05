from fi.alk.harness.simulator_voice import SIMULATOR_INSTRUCTIONS


def test_simulator_waits_for_final_confirmation_before_closing() -> None:
    assert "Wait for the complete question" in SIMULATOR_INSTRUCTIONS
    assert "booking summary is not a completed outcome" in SIMULATOR_INSTRUCTIONS
    assert "Do not use goodbye" in SIMULATOR_INSTRUCTIONS
    assert "Follow sequence words literally" in SIMULATOR_INSTRUCTIONS
    assert "do not reveal or request the later action" in SIMULATOR_INSTRUCTIONS

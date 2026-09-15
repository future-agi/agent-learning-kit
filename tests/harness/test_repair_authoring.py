from pydantic import ValidationError
import pytest

from fi.alk.harness.repair_authoring import _PatchSubmission


def test_world_ir_repair_submission_rejects_empty_patch() -> None:
    with pytest.raises(ValidationError):
        _PatchSubmission.model_validate({"operations": []})

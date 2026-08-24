from pathlib import Path

import pytest

from metaclass.modules.paper_workflow.validators import (
    PaperArtifactValidationError,
    PaperArtifactValidator,
)


def test_final_validator_diagnostic_returns_all_errors_without_deleting_artifacts(
    tmp_path: Path,
) -> None:
    final = tmp_path / "final"
    final.mkdir()
    analysis = final / "paper_analysis.json"
    analysis.write_text('{"workspace": "/Users/example/private-paper"}', encoding="utf-8")
    validator = PaperArtifactValidator()

    errors = validator.validate_final_directory(final, mode="diagnostic")

    assert analysis.is_file()
    assert any("missing required final artifact" in item for item in errors)
    assert any("absolute path" in item for item in errors)
    with pytest.raises(PaperArtifactValidationError):
        validator.validate_final_directory(final, mode="strict")

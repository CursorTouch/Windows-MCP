from pathlib import Path


def test_validation_artifacts_use_process_and_guid_unique_run_id() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "scripts" / "validate_project.ps1"
    ).read_text(encoding="utf-8")

    assert "yyyyMMdd-HHmmssfff" in source
    assert "$PID" in source
    assert "[guid]::NewGuid()" in source
    assert 'validacao-$timestamp.log' in source
    assert '$Name-$timestamp.stdout.log' in source
    assert '$Name-$timestamp.stderr.log' in source

def test_validation_logger_accepts_blank_native_output_lines() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "scripts" / "validate_project.ps1"
    ).read_text(encoding="utf-8")

    assert "[AllowEmptyString()] [string]$Text" in source

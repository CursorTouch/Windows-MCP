"""Issue #478: an indicator failure must say why the controller is unavailable."""

from windows_mcp.desktop.control import describe_failure as _describe_failure
from windows_mcp.desktop import control


def _owner(monkeypatch):
    monkeypatch.setattr(control.time, "monotonic", lambda: 100.0)
    owner = control.ControlCoordinator()
    owner._release_requested.set = lambda: None
    return owner


def test_fail_open_reason_is_reported_while_unavailable(monkeypatch):
    owner = _owner(monkeypatch)
    owner._fail_open(reason="AI control indicator failed: ImportError: blocked")
    snap = owner._snapshot_locked(100.0)
    assert snap["state"] == "unavailable"
    assert snap["reason"] == "AI control indicator failed: ImportError: blocked"


def test_fail_open_without_reason_keeps_previous_reason(monkeypatch):
    owner = _owner(monkeypatch)
    owner._fail_open(reason="first")
    owner._fail_open()
    assert owner._snapshot_locked(100.0)["reason"] == "first"


def test_no_reason_key_when_not_unavailable(monkeypatch):
    owner = _owner(monkeypatch)
    owner._fail_reason = "stale"
    owner._state = "user"
    assert "reason" not in owner._snapshot_locked(100.0)


def test_describe_failure_reports_innermost_cause():
    try:
        try:
            raise ImportError("DLL load failed while importing _imagingft")
        except ImportError as inner:
            raise RuntimeError("AI control indicator failed") from inner
    except RuntimeError as exc:
        text = _describe_failure(exc)
    assert text == "ImportError: DLL load failed while importing _imagingft"


def test_describe_failure_without_message():
    assert _describe_failure(MemoryError()) == "MemoryError"

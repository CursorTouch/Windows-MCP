"""Regression tests for issue #412 - recovering from a corrupted comtypes.gen cache.

#358 serialized cache generation behind a cross-process lock and cleared the
cache on ImportError/AttributeError. A generated module that was cut off
mid-write (a process killed during generation, or a pre-#358 race) does not
fail with ImportError, though: Python raises SyntaxError while compiling it
("unterminated string literal" in the #412 reports), and a damaged bytecode
file raises ValueError or EOFError. Those escaped safe_get_module, so every
later startup crashed on the same broken file instead of regenerating it.

These tests point comtypes.gen at a temporary package directory and use a
fake GetModule that writes and imports a module there, so the real
_clear_gen_cache runs against real files.
"""

import contextlib
import importlib
import sys
import types

import pytest

from windows_mcp.uia import comtypes_cache

MODULE_NAME = "FakeTypeLib"
FULL_SOURCE = 'IUIAutomation = "interface"\nUIA_IsOffscreenPropertyId = 30022\n'
# Cut off inside a string literal, as in the "unterminated string literal" report.
TRUNCATED_SOURCE = 'IUIAutomation = "interf'


@pytest.fixture
def gen_dir(tmp_path, monkeypatch):
    """Make comtypes.gen a throwaway package rooted at tmp_path/gen."""
    gen_path = tmp_path / "gen"
    gen_path.mkdir()
    init_file = gen_path / "__init__.py"
    init_file.write_text("")

    gen_pkg = types.ModuleType("comtypes.gen")
    gen_pkg.__file__ = str(init_file)
    gen_pkg.__path__ = [str(gen_path)]

    import comtypes

    monkeypatch.setitem(sys.modules, "comtypes.gen", gen_pkg)
    monkeypatch.setattr(comtypes, "gen", gen_pkg, raising=False)
    # The lock is msvcrt-based and covered by #358; these tests are about recovery.
    monkeypatch.setattr(comtypes_cache, "comtypes_cache_lock", contextlib.nullcontext)
    yield gen_path
    sys.modules.pop("comtypes.gen.%s" % MODULE_NAME, None)


class FakeGetModule:
    """Stand-in for comtypes.client.GetModule.

    Like comtypes, it only generates the module when it is not already in the
    cache, then imports it. ``sources`` lists what each generation writes.
    """

    def __init__(self, gen_path, sources):
        self.gen_path = gen_path
        self.sources = list(sources)
        self.calls = 0

    def __call__(self, tlib):
        self.calls += 1
        module_file = self.gen_path / ("%s.py" % MODULE_NAME)
        if not module_file.exists():
            module_file.write_text(self.sources.pop(0))
        importlib.invalidate_caches()
        return importlib.import_module("comtypes.gen.%s" % MODULE_NAME)


def install(monkeypatch, fake):
    monkeypatch.setattr(comtypes_cache.comtypes.client, "GetModule", fake)


def test_truncated_cache_file_is_cleared_and_regenerated(gen_dir, monkeypatch):
    """The #412 failure: a truncated module raises SyntaxError on import."""
    (gen_dir / ("%s.py" % MODULE_NAME)).write_text(TRUNCATED_SOURCE)
    (gen_dir / "_stale_dependency.py").write_text("x = 1\n")
    fake = FakeGetModule(gen_dir, [FULL_SOURCE])
    install(monkeypatch, fake)

    module = comtypes_cache.safe_get_module("FakeTypeLib.dll", required_attr="IUIAutomation")

    assert module.IUIAutomation == "interface"
    assert fake.calls == 2, "expected one failed load and one retry"
    assert (gen_dir / ("%s.py" % MODULE_NAME)).read_text() == FULL_SOURCE
    assert not (gen_dir / "_stale_dependency.py").exists(), "cache must be cleared"
    assert (gen_dir / "__init__.py").exists(), "package marker must survive clearing"


def test_truncated_file_written_by_first_generation_is_recovered(gen_dir, monkeypatch):
    """Generation itself produced a truncated file (cold cache)."""
    fake = FakeGetModule(gen_dir, [TRUNCATED_SOURCE, FULL_SOURCE])
    install(monkeypatch, fake)

    module = comtypes_cache.safe_get_module("FakeTypeLib.dll")

    assert module.UIA_IsOffscreenPropertyId == 30022
    assert fake.calls == 2


@pytest.mark.parametrize(
    "error",
    [
        SyntaxError("unterminated string literal"),
        ValueError("bad marshal data (unknown type code)"),
        EOFError("marshal data too short"),
        ImportError("cannot import name 'IUIAutomationCacheRequest'"),
    ],
    ids=["SyntaxError", "ValueError", "EOFError", "ImportError"],
)
def test_corruption_errors_trigger_one_clear_and_retry(error, gen_dir, monkeypatch):
    good = types.SimpleNamespace(IUIAutomation=object())
    calls = []

    def get_module(tlib):
        calls.append(tlib)
        if len(calls) == 1:
            raise error
        return good

    cleared = []
    monkeypatch.setattr(comtypes_cache.comtypes.client, "GetModule", get_module)
    monkeypatch.setattr(comtypes_cache, "_clear_gen_cache", lambda: cleared.append(True))

    assert comtypes_cache.safe_get_module("X.dll", required_attr="IUIAutomation") is good
    assert calls == ["X.dll", "X.dll"]
    assert cleared == [True]


def test_persistent_corruption_is_reraised_after_one_retry(gen_dir, monkeypatch):
    fake = FakeGetModule(gen_dir, [TRUNCATED_SOURCE, TRUNCATED_SOURCE])
    install(monkeypatch, fake)

    with pytest.raises(SyntaxError):
        comtypes_cache.safe_get_module("FakeTypeLib.dll")
    assert fake.calls == 2, "must retry exactly once, not loop"


def test_unrelated_errors_are_not_swallowed(gen_dir, monkeypatch):
    """Only cache-corruption errors trigger a clear; anything else propagates."""
    cleared = []

    def get_module(tlib):
        raise OSError("type library not registered")

    monkeypatch.setattr(comtypes_cache.comtypes.client, "GetModule", get_module)
    monkeypatch.setattr(comtypes_cache, "_clear_gen_cache", lambda: cleared.append(True))

    with pytest.raises(OSError):
        comtypes_cache.safe_get_module("X.dll")
    assert cleared == []


def test_module_missing_required_attr_is_regenerated(gen_dir, monkeypatch):
    partial = 'UIA_IsOffscreenPropertyId = 30022\n'  # imports fine, lacks IUIAutomation
    (gen_dir / ("%s.py" % MODULE_NAME)).write_text(partial)
    fake = FakeGetModule(gen_dir, [FULL_SOURCE])
    install(monkeypatch, fake)

    module = comtypes_cache.safe_get_module("FakeTypeLib.dll", required_attr="IUIAutomation")

    assert module.IUIAutomation == "interface"
    assert fake.calls == 2

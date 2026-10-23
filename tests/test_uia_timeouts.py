"""Bounded UI Automation timeouts: a provider that stops answering must fail fast, not stall the UIA lane.

Covers the pieces of the change:
  * the shared client is created from CUIAutomation8 with the configured timeouts, and falls back
    to the v1 CUIAutomation client when that fails;
  * ERROR_TIMEOUT (0x800705B4) is a typed UIATimeoutError, and is_timeout_exception() recognises
    both typed and raw COMError timeouts;
  * WINDOWS_MCP_UIA_*_TIMEOUT_MS parsing;
  * Tree.get_window_wise_nodes() reports a timed-out window as failed after ONE attempt, without
    the backoff retries and without the extra ControlFromHandle(...).Name lookup;
  * CachedControlHelper.get_cached_children() does not pay a timeout twice via its uncached fallback;
  * Desktop.get_active_window() stops retrying on a timeout.
"""

import os
from types import SimpleNamespace
from unittest import mock

from _ctypes import COMError

from windows_mcp.uia import (
    UIAError,
    UIADeadElementError,
    UIATimeoutError,
    from_com_error,
    is_timeout_error,
    is_timeout_exception,
)
from windows_mcp.uia import core as uia_core
from windows_mcp.uia.core import (
    DEFAULT_UIA_CONNECTION_TIMEOUT_MS,
    DEFAULT_UIA_TRANSACTION_TIMEOUT_MS,
    _uia_timeout_ms,
)
from windows_mcp.tree import service as tree_service
from windows_mcp.tree.budget import TreeElementBudget
from windows_mcp.tree.cache_utils import CachedControlHelper
from windows_mcp.desktop.service import Desktop

ERROR_TIMEOUT = -2147023436  # 0x800705B4


def _com_error(code: int) -> COMError:
    return COMError(code, "test", (None, None, None, 0, None))


# --- error mapping ---------------------------------------------------------------------------

def test_error_timeout_is_a_typed_timeout():
    assert UIAError.ERROR_TIMEOUT == ERROR_TIMEOUT
    assert isinstance(from_com_error(_com_error(ERROR_TIMEOUT)), UIATimeoutError)


def test_all_timeout_codes_are_recognised():
    for code in (UIAError.UIA_E_TIMEOUT, UIAError.RPC_E_TIMEOUT, UIAError.ERROR_TIMEOUT):
        assert is_timeout_error(code)
    assert not is_timeout_error(UIAError.UIA_E_ELEMENTNOTAVAILABLE)


def test_is_timeout_exception_accepts_raw_and_typed_errors():
    assert is_timeout_exception(_com_error(ERROR_TIMEOUT))
    assert is_timeout_exception(UIATimeoutError(ERROR_TIMEOUT))
    assert not is_timeout_exception(_com_error(UIAError.UIA_E_ELEMENTNOTAVAILABLE))
    assert not is_timeout_exception(UIADeadElementError(UIAError.UIA_E_ELEMENTNOTAVAILABLE))
    assert not is_timeout_exception(ValueError("not a COM error"))


# --- timeout configuration -------------------------------------------------------------------

def test_uia_timeout_ms_reads_positive_integers():
    assert _uia_timeout_ms("X", 2000, {"X": "750"}) == 750
    assert _uia_timeout_ms("X", 2000, {"X": " 1200 "}) == 1200


def test_uia_timeout_ms_falls_back_on_unset_or_invalid_values():
    for env in ({}, {"X": ""}, {"X": "abc"}, {"X": "0"}, {"X": "-5"}, {"X": "1.5"}):
        assert _uia_timeout_ms("X", 2000, env) == 2000


def test_defaults_are_bounded():
    assert 0 < DEFAULT_UIA_CONNECTION_TIMEOUT_MS <= DEFAULT_UIA_TRANSACTION_TIMEOUT_MS <= 10_000


# --- shared client creation ------------------------------------------------------------------

V1_CLSID = "{ff48dba4-60ef-4201-aa87-54103eef594e}"


def _bare_client():
    client = object.__new__(uia_core._AutomationClient)
    client.UIAutomationCore = SimpleNamespace(
        CUIAutomation8="CUIAutomation8", IUIAutomation2="IUIAutomation2", IUIAutomation="IUIAutomation"
    )
    return client


def test_client_uses_cuiautomation8_with_configured_timeouts():
    created = []

    def create_object(progid, interface):
        created.append((progid, interface))
        return SimpleNamespace(ConnectionTimeout=None, TransactionTimeout=None)

    env = {"WINDOWS_MCP_UIA_CONNECTION_TIMEOUT_MS": "1500", "WINDOWS_MCP_UIA_TRANSACTION_TIMEOUT_MS": "7000"}
    with mock.patch.object(uia_core.comtypes.client, "CreateObject", create_object), \
            mock.patch.dict(os.environ, env):
        client = _bare_client()._create_client()

    assert created == [("CUIAutomation8", "IUIAutomation2")]
    assert (client.ConnectionTimeout, client.TransactionTimeout) == (1500, 7000)


def test_client_defaults_apply_when_env_is_unset():
    with mock.patch.object(uia_core.comtypes.client, "CreateObject",
                           lambda progid, interface: SimpleNamespace()), \
            mock.patch.dict(os.environ, {}, clear=True):
        client = _bare_client()._create_client()

    assert client.ConnectionTimeout == DEFAULT_UIA_CONNECTION_TIMEOUT_MS
    assert client.TransactionTimeout == DEFAULT_UIA_TRANSACTION_TIMEOUT_MS


def test_client_falls_back_to_v1_when_cuiautomation8_fails():
    for failure in (_com_error(-2147221164), OSError("class not registered"), AttributeError("CUIAutomation8")):
        created = []

        def create_object(progid, interface):
            created.append((progid, interface))
            if progid == "CUIAutomation8":
                raise failure
            return "v1-client"

        with mock.patch.object(uia_core.comtypes.client, "CreateObject", create_object):
            assert _bare_client()._create_client() == "v1-client"
        assert created == [("CUIAutomation8", "IUIAutomation2"), (V1_CLSID, "IUIAutomation")]


def test_shared_client_is_built_through_create_client():
    core_module = SimpleNamespace(
        CUIAutomation8="CUIAutomation8", IUIAutomation2="IUIAutomation2", IUIAutomation="IUIAutomation"
    )
    created = []

    def create_object(progid, interface):
        created.append(progid)
        return SimpleNamespace(RawViewWalker="walker")

    with mock.patch.object(uia_core, "safe_get_module", lambda *a, **k: core_module), \
            mock.patch.object(uia_core.comtypes.client, "CreateObject", create_object):
        client = uia_core._AutomationClient()

    assert created == ["CUIAutomation8"]
    assert client.IUIAutomation.TransactionTimeout >= 1
    assert client.ViewWalker == "walker"


# --- cached children -------------------------------------------------------------------------

def _node_whose_cache_build_fails(error):
    fallback_calls = []

    def build_updated_cache(_request):
        raise error

    def get_children():
        fallback_calls.append(1)
        return ["uncached-child"]

    node = SimpleNamespace(Element=SimpleNamespace(BuildUpdatedCache=build_updated_cache), GetChildren=get_children)
    return node, fallback_calls


def test_cached_children_timeout_is_not_paid_again_by_the_fallback():
    node, fallback_calls = _node_whose_cache_build_fails(_com_error(ERROR_TIMEOUT))
    try:
        CachedControlHelper.get_cached_children(node, mock.MagicMock())
    except COMError as e:
        assert is_timeout_exception(e)
    else:
        raise AssertionError("timeout was swallowed")
    assert fallback_calls == []


def test_cached_children_other_errors_still_fall_back():
    node, fallback_calls = _node_whose_cache_build_fails(_com_error(-2147467259))  # E_FAIL
    assert CachedControlHelper.get_cached_children(node, mock.MagicMock()) == ["uncached-child"]
    assert fallback_calls == [1]


# --- tree capture ----------------------------------------------------------------------------

def _bare_tree():
    tree = object.__new__(tree_service.Tree)
    tree.desktop = SimpleNamespace(is_window_browser=lambda node: False)
    tree.element_budget = TreeElementBudget(500)
    return tree


def test_timed_out_window_is_not_retried():
    tree = _bare_tree()
    lookups = []

    def control_from_handle(handle):
        lookups.append(handle)
        return SimpleNamespace(ClassName="Chrome_WidgetWin_1", Name="hung")

    attempts = []

    def get_nodes(handle, is_browser, wait_time=0, use_dom=False):
        attempts.append(handle)
        if handle == 1:
            raise UIATimeoutError(ERROR_TIMEOUT)
        return [], [], [], None

    with mock.patch.object(tree_service, "ControlFromHandle", control_from_handle), \
            mock.patch.object(tree, "get_nodes", get_nodes, create=True), \
            mock.patch.object(tree_service, "sleep") as sleep:
        result = tree.get_window_wise_nodes(windows_handles=[1, 2], active_window_flag=False)

    failed_handles = result[3]
    assert failed_handles == [1]
    assert attempts == [1, 2]          # one attempt for the hung window, the next window still captured
    assert lookups == [1, 2]           # no extra Name lookup into the hung window
    sleep.assert_not_called()          # no backoff


def test_window_that_times_out_before_traversal_is_skipped():
    tree = _bare_tree()

    def control_from_handle(handle):
        if handle == 1:
            raise _com_error(ERROR_TIMEOUT)
        return SimpleNamespace(ClassName="Notepad", Name="ok")

    attempts = []

    def get_nodes(handle, is_browser, wait_time=0, use_dom=False):
        attempts.append(handle)
        return [], [], [], None

    with mock.patch.object(tree_service, "ControlFromHandle", control_from_handle), \
            mock.patch.object(tree, "get_nodes", get_nodes, create=True):
        result = tree.get_window_wise_nodes(windows_handles=[1, 2], active_window_flag=False)

    assert result[3] == [1]
    assert attempts == [2]


def test_other_errors_keep_the_existing_retry_policy():
    tree = _bare_tree()
    attempts = []

    def get_nodes(handle, is_browser, wait_time=0, use_dom=False):
        attempts.append(handle)
        raise RuntimeError("transient")

    with mock.patch.object(tree_service, "ControlFromHandle", lambda h: SimpleNamespace(ClassName="X", Name="x")), \
            mock.patch.object(tree, "get_nodes", get_nodes, create=True), \
            mock.patch.object(tree_service, "sleep"):
        result = tree.get_window_wise_nodes(windows_handles=[7], active_window_flag=False)

    assert result[3] == [7]
    assert len(attempts) == tree_service.THREAD_MAX_RETRIES + 1


# --- active window ---------------------------------------------------------------------------

def test_active_window_stops_retrying_on_timeout():
    calls = []

    def foreground():
        calls.append(1)
        raise _com_error(ERROR_TIMEOUT)

    fake = SimpleNamespace(
        _UIA_RETRIES=3,
        _UIA_RETRY_SLEEP_MS=0,
        get_windows=lambda: ([], set()),
        get_foreground_window=foreground,
    )
    assert Desktop.get_active_window(fake, windows=[]) is None
    assert len(calls) == 1


def test_active_window_still_retries_transient_errors():
    calls = []

    def foreground():
        calls.append(1)
        raise _com_error(UIAError.RPC_E_CALL_REJECTED)

    fake = SimpleNamespace(
        _UIA_RETRIES=3,
        _UIA_RETRY_SLEEP_MS=0,
        get_windows=lambda: ([], set()),
        get_foreground_window=foreground,
    )
    assert Desktop.get_active_window(fake, windows=[]) is None
    assert len(calls) == 3

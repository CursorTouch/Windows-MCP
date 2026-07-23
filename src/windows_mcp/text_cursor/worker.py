"""Single-threaded COM worker state for TextCursor operations."""

import ctypes
import threading
from concurrent.futures import ThreadPoolExecutor

from .uia import UIAAutomationObject, UIAModule, create_automation

COINIT_MULTITHREADED = 0x0
RPC_E_CHANGED_MODE = 0x80010106

EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="uia-text-cursor",
)

# Per-worker-thread COM state. The executor thread lives for the process
# lifetime, so COM is initialized once and the IUIAutomation client is cached
# and reused across calls instead of being recreated (and the apartment
# re-initialized) on every invocation.
_thread_state = threading.local()


def co_initialize_mta() -> bool:
    ole32 = ctypes.windll.ole32

    ole32.CoInitializeEx.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    ole32.CoInitializeEx.restype = ctypes.c_long

    hr = int(
        ole32.CoInitializeEx(
            None,
            COINIT_MULTITHREADED,
        )
    )

    unsigned_hr = hr & 0xFFFFFFFF

    if unsigned_hr == RPC_E_CHANGED_MODE:
        raise RuntimeError("The UIA worker thread has an incompatible COM apartment.")

    if unsigned_hr & 0x80000000:
        raise OSError(f"CoInitializeEx failed: 0x{unsigned_hr:08X}")

    return True


def get_thread_automation() -> tuple[UIAModule, UIAAutomationObject]:
    """Return this worker thread's cached UIA client, creating it on first use.

    COM is initialized (MTA) exactly once per thread and the IUIAutomation
    client is cached, so repeated calls reuse the same client rather than
    paying for CoInitializeEx + CreateObject on every invocation. The client
    is long-lived and never goes stale; only the focused element and its text
    ranges are re-fetched per call.
    """
    if not getattr(_thread_state, "com_initialized", False):
        co_initialize_mta()
        _thread_state.com_initialized = True

    automation = getattr(_thread_state, "automation", None)
    if automation is None:
        automation = create_automation()
        _thread_state.automation = automation

    return automation

"""UIA exception hierarchy for typed error handling."""

from _ctypes import COMError  # noqa: F401 — re-exported for callers
from .enums import UIAError


class UIAException(Exception):
    """Base exception for all UIA/COM errors. Wraps ctypes.COMError with a typed error code."""

    def __init__(self, code: int, original: Exception | None = None):
        self.code = code
        self.original = original
        name = UIAError(code).name if code in UIAError._value2member_map_ else hex(code & 0xFFFFFFFF)
        super().__init__(f"{name} ({code})")

    @property
    def uia_error(self) -> UIAError | None:
        return UIAError(self.code) if self.code in UIAError._value2member_map_ else None


class UIADeadElementError(UIAException):
    """Element or window no longer exists — retrying will not help.

    Covers: UIA_E_ELEMENTNOTAVAILABLE, RO_E_CLOSED, RPC_E_DISCONNECTED,
            RPC_E_SERVER_DIED, RPC_E_SERVER_DIED_DNE, RPC_E_CONNECTION_TERMINATED,
            CO_E_OBJNOTCONNECTED, CO_E_RELEASED.
    """


class UIARetryableError(UIAException):
    """App is busy but alive — the call may succeed on retry.

    Covers: RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER, RPC_E_SERVERCALL_REJECTED.
    """


class UIANotEnabledError(UIAException):
    """Method called on a disabled element (UIA_E_ELEMENTNOTENABLED)."""


class UIANotSupportedError(UIAException):
    """Property or pattern not supported by the provider (UIA_E_NOTSUPPORTED)."""


class UIANoClickablePointError(UIAException):
    """Element has no clickable point (UIA_E_NOCLICKABLEPOINT)."""


class UIATimeoutError(UIAException):
    """UIA or RPC operation timed out (UIA_E_TIMEOUT, RPC_E_TIMEOUT)."""


class UIAThreadError(UIAException):
    """Threading violation — wrong thread or CoInitialize not called.

    Covers: RPC_E_WRONG_THREAD, RPC_E_THREAD_NOT_INIT, RPC_E_CHANGED_MODE.
    """


class UIAInvalidOperationError(UIAException):
    """Operation is not valid in the current state (UIA_E_INVALIDOPERATION)."""


class UIAAccessDeniedError(UIAException):
    """Access denied to the element or operation (E_ACCESSDENIED, RPC_E_ACCESS_DENIED)."""


class UIAUnknownError(UIAException):
    """COM error with no specific UIA mapping."""


_CODE_TO_EXCEPTION: dict[int, type[UIAException]] = {
    UIAError.UIA_E_ELEMENTNOTAVAILABLE: UIADeadElementError,
    UIAError.RO_E_CLOSED: UIADeadElementError,
    UIAError.RPC_E_DISCONNECTED: UIADeadElementError,
    UIAError.RPC_E_SERVER_DIED: UIADeadElementError,
    UIAError.RPC_E_SERVER_DIED_DNE: UIADeadElementError,
    UIAError.RPC_E_CONNECTION_TERMINATED: UIADeadElementError,
    UIAError.CO_E_OBJNOTCONNECTED: UIADeadElementError,
    UIAError.CO_E_RELEASED: UIADeadElementError,
    UIAError.RPC_E_CALL_REJECTED: UIARetryableError,
    UIAError.RPC_E_SERVERCALL_RETRYLATER: UIARetryableError,
    UIAError.RPC_E_SERVERCALL_REJECTED: UIARetryableError,
    UIAError.UIA_E_ELEMENTNOTENABLED: UIANotEnabledError,
    UIAError.UIA_E_NOTSUPPORTED: UIANotSupportedError,
    UIAError.UIA_E_NOCLICKABLEPOINT: UIANoClickablePointError,
    UIAError.UIA_E_TIMEOUT: UIATimeoutError,
    UIAError.RPC_E_TIMEOUT: UIATimeoutError,
    UIAError.UIA_E_INVALIDOPERATION: UIAInvalidOperationError,
    UIAError.RPC_E_WRONG_THREAD: UIAThreadError,
    UIAError.RPC_E_THREAD_NOT_INIT: UIAThreadError,
    UIAError.RPC_E_CHANGED_MODE: UIAThreadError,
    UIAError.E_ACCESSDENIED: UIAAccessDeniedError,
    UIAError.RPC_E_ACCESS_DENIED: UIAAccessDeniedError,
}


def from_com_error(e: COMError) -> UIAException:
    """Convert a ctypes.COMError into a typed UIAException. Only call with COMError instances."""
    code: int = e.args[0] if e.args else 0
    exc_class = _CODE_TO_EXCEPTION.get(code, UIAUnknownError)
    return exc_class(code, e)

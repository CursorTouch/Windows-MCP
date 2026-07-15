from windows_mcp.uia.core import _AutomationClient
import comtypes
import logging
import weakref

# Get UIA Interface for COM definitions
uia_client = _AutomationClient.instance()
UIA = uia_client.UIAutomationCore

logger = logging.getLogger(__name__)


def _record_failure(parent, counter_attr: str) -> None:
    """Bump parent's consecutive-failure counter and request a client rebuild
    once it crosses FAIL_THRESHOLD. Never touches AddXxx/RemoveXxx here -
    mutating UIA subscriptions from inside a COM callback still on the call
    stack risks reentrancy; the owning _run loop does the actual rebuild."""
    from .service import FAIL_THRESHOLD

    count = getattr(parent, counter_attr, 0) + 1
    setattr(parent, counter_attr, count)
    if count >= FAIL_THRESHOLD:
        parent.request_rebuild()


class FocusChangedEventHandler(comtypes.COMObject):
    _com_interfaces_ = [UIA.IUIAutomationFocusChangedEventHandler]

    def __init__(self, parent):
        self._parent = weakref.ref(parent)
        super(FocusChangedEventHandler, self).__init__()

    def HandleFocusChangedEvent(self, sender):
        parent = self._parent()
        try:
            if parent and parent._focus_callback:
                parent._focus_callback(sender)
            if parent:
                parent._focus_fail_count = 0
        except comtypes.COMError as e:
            if parent:
                _record_failure(parent, "_focus_fail_count")
            logger.debug("Focus callback COM error: %s", e)
        except Exception as e:
            logger.debug("Focus callback error: %s", e)
        return 0  # S_OK


class StructureChangedEventHandler(comtypes.COMObject):
    _com_interfaces_ = [UIA.IUIAutomationStructureChangedEventHandler]

    def __init__(self, parent):
        self._parent = weakref.ref(parent)
        super(StructureChangedEventHandler, self).__init__()

    def HandleStructureChangedEvent(self, sender, changeType, runtimeId):
        parent = self._parent()
        try:
            if parent and parent._structure_callback:
                parent._structure_callback(sender, changeType, runtimeId)
            if parent:
                parent._structure_fail_count = 0
        except comtypes.COMError as e:
            if parent:
                _record_failure(parent, "_structure_fail_count")
            logger.debug("Structure callback COM error: %s", e)
        except Exception as e:
            logger.debug("Structure callback error: %s", e)
        return 0  # S_OK


class PropertyChangedEventHandler(comtypes.COMObject):
    _com_interfaces_ = [UIA.IUIAutomationPropertyChangedEventHandler]

    def __init__(self, parent):
        self._parent = weakref.ref(parent)
        super(PropertyChangedEventHandler, self).__init__()

    def HandlePropertyChangedEvent(self, sender, propertyId, newValue):
        parent = self._parent()
        try:
            if parent and parent._property_callback:
                parent._property_callback(sender, propertyId, newValue)
            if parent:
                parent._property_fail_count = 0
        except comtypes.COMError as e:
            if parent:
                _record_failure(parent, "_property_fail_count")
            logger.debug("Property callback COM error: %s", e)
        except Exception as e:
            logger.debug("Property callback error: %s", e)
        return 0  # S_OK

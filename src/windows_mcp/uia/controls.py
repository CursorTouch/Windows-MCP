"""
uiautomation for Python 3.
Author: yinkaisheng
Source: https://github.com/yinkaisheng/Python-UIAutomation-for-Windows

This module is for UIAutomation on Windows(Windows XP with SP3, Windows Vista and Windows 7/8/8.1/10).
It supports UIAutomation for the applications which implemented IUIAutomation, such as MFC, Windows Form, WPF, Modern UI(Metro UI), Qt, Firefox and Chrome.
Run 'automation.py -h' for help.

uiautomation is shared under the Apache Licene 2.0.
This means that the code can be freely copied and distributed, and costs nothing to use.
"""

from __future__ import annotations

import os
import sys
import time
import datetime
import re
import threading
import ctypes
import ctypes.wintypes
import comtypes
from _ctypes import COMError
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple
from .enums import *
from .core import *
from .core import _AutomationClient, _get_system_dpi
from .patterns import *
from .exceptions import from_com_error, UIAException


METRO_WINDOW_CLASS_NAME = "Windows.UI.Core.CoreWindow"  # for Windows 8 and 8.1
SEARCH_INTERVAL = 0.5  # search control interval seconds
MAX_MOVE_SECOND = 1  # simulate mouse move or drag max seconds
TIME_OUT_SECOND = 10
OPERATION_WAIT_TIME = 0.5
MAX_PATH = 260
DEBUG_SEARCH_TIME = False
DEBUG_EXIST_DISAPPEAR = False
S_OK = 0

IsPy38OrHigher = sys.version_info[:2] >= (3, 8)
IsNT6orHigher = os.sys.getwindowsversion().major >= 6
CurrentProcessIs64Bit = sys.maxsize > 0xFFFFFFFF
ProcessTime = time.perf_counter  # this returns nearly 0 when first call it if python version <= 3.6
ProcessTime()  # need to call it once if python version <= 3.6
TreeNode = Any


class _ViewWalkerState:
    """Tracks whether this process's UIA tree walker actually returns children.

    On some configurations — Windows ARM64 running x86-emulated Python is the
    reported one — `RawViewWalker.GetFirstChildElement` returns NULL for every
    element while `IUIAutomationElement::FindAll` on the same element enumerates
    its children normally. That turns every window into an apparent leaf and
    empties out window enumeration and the whole accessibility tree.

    Rather than pay for a redundant `FindAll` on every genuinely childless leaf,
    cross-check only the first few empty walks. One `FindAll` that finds children
    where the walker found none is proof the walker is broken, and from then on
    `GetChildren` skips the walker entirely.
    """

    #: How many empty walks to cross-check before concluding the walker is fine.
    #: On a healthy system this costs at most this many extra `FindAll` calls for
    #: the lifetime of the process; on a broken one the first probe latches.
    MAX_PROBES = 3

    _lock = threading.Lock()
    _broken = False
    _probes = 0

    @classmethod
    def is_broken(cls) -> bool:
        """Return True once the walker has been proven to return no children."""
        return cls._broken

    @classmethod
    def should_probe(cls) -> bool:
        """Return True if an empty walk is still worth cross-checking with FindAll."""
        return not cls._broken and cls._probes < cls.MAX_PROBES

    @classmethod
    def record_probe(cls, walker_is_broken: bool) -> None:
        """Record the outcome of one cross-check.

        walker_is_broken: bool, True if FindAll returned children the walker missed.
        """
        with cls._lock:
            cls._probes += 1
            if walker_is_broken:
                cls._broken = True

    @classmethod
    def reset(cls) -> None:
        """Clear the latched state. For tests."""
        with cls._lock:
            cls._broken = False
            cls._probes = 0


class _PropertyNotSupported:
    """Sentinel type for a UI Automation property the provider does not implement.

    Distinct from `None`: `None` is a legitimate value for several properties (an
    unset `LabeledBy`, for instance), whereas this means the provider never
    implemented the property at all.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "PROPERTY_NOT_SUPPORTED"


PROPERTY_NOT_SUPPORTED = _PropertyNotSupported()

# Properties whose value is an IUIAutomationElementArray behind a POINTER(IUnknown).
ELEMENT_ARRAY_PROPERTY_IDS = frozenset(
    [
        PropertyId.AnnotationObjectsProperty,
        PropertyId.ControllerForProperty,
        PropertyId.DescribedByProperty,
        PropertyId.DragGrabbedItemsProperty,
        PropertyId.FlowsFromProperty,
        PropertyId.FlowsToProperty,
        PropertyId.LegacyIAccessibleSelectionProperty,
        PropertyId.SelectionSelectionProperty,
        PropertyId.SpreadsheetItemAnnotationObjectsProperty,
        PropertyId.TableColumnHeadersProperty,
        PropertyId.TableItemColumnHeaderItemsProperty,
        PropertyId.TableItemRowHeaderItemsProperty,
        PropertyId.TableRowHeadersProperty,
    ]
)

# Properties whose value is a single IUIAutomationElement behind a POINTER(IUnknown).
ELEMENT_PROPERTY_IDS = frozenset(
    [
        PropertyId.LabeledByProperty,
        PropertyId.SelectionItemSelectionContainerProperty,
    ]
)

_reservedNotSupportedAddress: Optional[int] = None


def _get_reserved_not_supported_address() -> int:
    """
    Return int, the address of UI Automation's shared "not supported" sentinel.

    `GetCurrentPropertyValueEx(pid, 1)` returns this exact singleton pointer for every
    unimplemented property, so identity by address is the reliable test -- the object
    itself is an opaque IUnknown with no comparable value. Cached on first use.
    """
    global _reservedNotSupportedAddress
    if _reservedNotSupportedAddress is None:
        try:
            reserved = _AutomationClient.instance().IUIAutomation.ReservedNotSupportedValue
            _reservedNotSupportedAddress = (
                ctypes.cast(reserved, ctypes.c_void_p).value or 0
            )
        except (COMError, AttributeError, ValueError):
            _reservedNotSupportedAddress = 0
    return _reservedNotSupportedAddress


def _is_reserved_value(value: Any, reservedAddress: int) -> bool:
    """Return bool, True if `value` is the shared "not supported" sentinel pointer."""
    if not reservedAddress or not isinstance(value, ctypes.POINTER(comtypes.IUnknown)):
        return False
    try:
        return ctypes.cast(value, ctypes.c_void_p).value == reservedAddress
    except (ctypes.ArgumentError, ValueError):
        return False


def _normalize_property_value(propertyId: int, value: Any, resolveElements: bool) -> Any:
    """
    Convert a raw UI Automation property value into a friendly Python value.

    Handles the three raw shapes that are not already plain Python: the 4-double
    bounding rectangle tuple, element arrays, and single element references.
    """
    if propertyId == PropertyId.BoundingRectangleProperty and isinstance(value, tuple):
        if len(value) == 4:
            left, top, width, height = value
            return Rect(
                left=int(left),
                top=int(top),
                right=int(left) + int(width),
                bottom=int(top) + int(height),
            )
        return value
    if resolveElements and propertyId in ELEMENT_ARRAY_PROPERTY_IDS:
        return Control.CreateControlsFromRawElementArray(value)
    if resolveElements and propertyId in ELEMENT_PROPERTY_IDS:
        return Control.CreateControlFromRawElement(value)
    return value


class Control:
    ValidKeys = set(
        [
            "ControlType",
            "ClassName",
            "AutomationId",
            "Name",
            "SubName",
            "RegexName",
            "Depth",
            "Compare",
        ]
    )

    def __init__(
        self,
        searchFromControl: "Control" | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        ControlType: int | None = None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        """
        searchFromControl: `Control` or its subclass, if it is None, search from root control(Desktop).
        searchDepth: int, max search depth from searchFromControl.
        foundIndex: int, starts with 1, >= 1.
        searchInterval: float, wait searchInterval after every search in self.Refind and self.Exists, the global timeout is TIME_OUT_SECOND.
        element: `ctypes.POINTER(IUIAutomationElement)`, internal use only.

        ControlType: int, a value in class `ControlType`.
        Name: str.
        SubName: str, a part str in Name.
        RegexName: str, supports regex using re.match.
            You can only use one of Name, SubName, RegexName.
        ClassName: str.
        AutomationId: str.
        Depth: int, only search controls in relative depth from searchFromControl, ignore controls in depth(0~Depth-1),
            if set, searchDepth will be set to Depth too.
        Compare: Callable[[Control, int], bool], custom compare function(control: Control, depth: int) -> bool.

        searchProperties, other properties specified for the control, only for debug log.

        `Control` wraps IUIAutomationElement.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationelement
        """
        self._element = element
        self._elementDirectAssign = True if element else False
        self.searchFromControl = searchFromControl
        self.searchDepth = Depth or searchDepth
        self.searchInterval = searchInterval
        self.foundIndex = foundIndex
        self.searchProperties = searchProperties
        if Name is not None:
            searchProperties["Name"] = Name
        if SubName is not None:
            searchProperties["SubName"] = SubName
        if RegexName is not None:
            searchProperties["RegexName"] = RegexName
            self.regexName = re.compile(RegexName)
        else:
            self.regexName = None
        if ClassName is not None:
            searchProperties["ClassName"] = ClassName
        if AutomationId is not None:
            searchProperties["AutomationId"] = AutomationId
        if ControlType is not None:
            searchProperties["ControlType"] = ControlType
        if Depth is not None:
            searchProperties["Depth"] = Depth
        if Compare is not None:
            searchProperties["Compare"] = Compare
        self._supportedPatterns = {}

    def __str__(self) -> str:
        return "ControlType: {0}    ClassName: {1}    AutomationId: {2}    Rect: {3}    Name: {4}    Handle: 0x{5:X}({5})".format(
            self.ControlTypeName,
            self.ClassName,
            self.AutomationId,
            self.BoundingRectangle,
            self.Name,
            self.NativeWindowHandle,
        )

    def __repr__(self) -> str:
        return (
            "<{0} ClassName={1!r} AutomationId={2} Rect={3} Name={4!r} Handle=0x{5:X}({5})>".format(
                self.__class__.__name__,
                self.ClassName,
                self.AutomationId,
                self.BoundingRectangle,
                self.Name,
                self.NativeWindowHandle,
            )
        )

    def __getitem__(self, pos: int) -> "Control" | None:
        if pos == 1:
            return self.GetFirstChildControl()
        elif pos == -1:
            return self.GetLastChildControl()
        elif pos > 1:
            child = self.GetFirstChildControl()
            for _ in range(pos - 1):
                if child is None:
                    return None
                child = child.GetNextSiblingControl()
            return child
        elif pos < -1:
            child = self.GetLastChildControl()
            for _ in range(-pos - 1):
                if child is None:
                    return None
                child = child.GetPreviousSiblingControl()
            return child
        else:
            raise ValueError

    @staticmethod
    def CreateControlFromElement(
        element: "ctypes.POINTER(IUIAutomationElement)",
    ) -> "Control" | None:
        """
        Create a concreate `Control` from a com type `IUIAutomationElement`.
        element: `ctypes.POINTER(IUIAutomationElement)`.
        Return a subclass of `Control`, an instance of the control's real type.
        """
        if element:
            controlType = element.CurrentControlType
            if controlType in ControlConstructors:
                return ControlConstructors[controlType](element=element)
            else:
                pass
        return None

    @staticmethod
    def CreateControlsFromRawElementArray(rawValue: Any) -> List["Control"]:
        """
        Convert a raw element-array property value into a list of `Control`.

        Element-array properties (`SelectionSelectionProperty`, `ControllerForProperty`,
        `FlowsToProperty`, `TableRowHeadersProperty`, ...) come back from
        `GetPropertyValue`/`GetCachedPropertyValue` as a bare `POINTER(IUnknown)` that
        still needs a QueryInterface to `IUIAutomationElementArray`. Note the pointer is
        non-NULL even when the array is empty, so truthiness of the raw value says
        nothing about whether the property actually has entries.

        rawValue: the raw property value, or None.
        Return List[Control], empty if rawValue is None, not an element array, or empty.
        """
        if not rawValue:
            return []
        try:
            elementArray = rawValue.QueryInterface(
                _AutomationClient.instance().UIAutomationCore.IUIAutomationElementArray
            )
        except (COMError, ValueError, AttributeError):
            return []
        controls: List["Control"] = []
        for i in range(elementArray.Length):
            try:
                control = Control.CreateControlFromElement(elementArray.GetElement(i))
            except COMError:
                continue
            if control:
                controls.append(control)
        return controls

    @staticmethod
    def CreateControlFromRawElement(rawValue: Any) -> "Control" | None:
        """
        Convert a raw single-element property value (e.g. `LabeledByProperty`) into a
        `Control`, QueryInterface-ing the `POINTER(IUnknown)` to `IUIAutomationElement`.

        rawValue: the raw property value, or None.
        Return `Control` or None if rawValue is None or is not an element.
        """
        if not rawValue:
            return None
        try:
            element = rawValue.QueryInterface(
                _AutomationClient.instance().UIAutomationCore.IUIAutomationElement
            )
        except (COMError, ValueError, AttributeError):
            return None
        try:
            return Control.CreateControlFromElement(element)
        except COMError:
            return None

    @staticmethod
    def CreateControlFromControl(control: "Control") -> "Control" | None:
        """
        Create a concreate `Control` from a control instance, copy it.
        control: `Control` or its subclass.
        Return a subclass of `Control`, an instance of the control's real type.
        For example: if control's ControlType is EditControl, return an EditControl.
        """
        newControl = Control.CreateControlFromElement(control.Element)
        return newControl

    def SetSearchFromControl(self, searchFromControl: "Control") -> None:
        """searchFromControl: `Control` or its subclass"""
        self.searchFromControl = searchFromControl

    def SetSearchDepth(self, searchDepth: int) -> None:
        self.searchDepth = searchDepth

    def AddSearchProperties(self, **searchProperties) -> None:
        """
        Add search properties using `dict.update`.
        searchProperties: dict, same as searchProperties in `Control.__init__`.
        """
        self.searchProperties.update(searchProperties)
        if "Depth" in searchProperties:
            self.searchDepth = searchProperties["Depth"]
        if "RegexName" in searchProperties:
            regName = searchProperties["RegexName"]
            self.regexName = re.compile(regName) if regName else None

    def RemoveSearchProperties(self, **searchProperties) -> None:
        """
        searchProperties: dict, same as searchProperties in `Control.__init__`.
        """
        for key in searchProperties:
            del self.searchProperties[key]
            if key == "RegexName":
                self.regexName = None

    def GetSearchPropertiesStr(self) -> str:
        strs = [
            "{}: {}".format(k, ControlTypeNames[v] if k == "ControlType" else repr(v))
            for k, v in self.searchProperties.items()
        ]
        return "{" + ", ".join(strs) + "}"

    def GetColorfulSearchPropertiesStr(self, keyColor="DarkGreen", valueColor="DarkCyan") -> str:
        """keyColor, valueColor: str, color name in class ConsoleColor"""
        strs = [
            "<Color={}>{}</Color>: <Color={}>{}</Color>".format(
                keyColor if k in Control.ValidKeys else "DarkYellow",
                k,
                valueColor,
                ControlTypeNames[v] if k == "ControlType" else repr(v),
            )
            for k, v in self.searchProperties.items()
        ]
        return "{" + ", ".join(strs) + "}"

    def BuildUpdatedCache(self, cacheRequest: "CacheRequest") -> "Control":
        """
        Retrieves a new UI Automation element with an updated cache.
        cacheRequest: CacheRequest.
        Return a subclass of `Control`, an instance of the control's real type.
        """
        updatedElement = self.Element.BuildUpdatedCache(cacheRequest.check_request)
        return Control.CreateControlFromElement(updatedElement)

    @property
    def CachedAcceleratorKey(self) -> str:
        """Get the cached accelerator key."""
        return self.Element.CachedAcceleratorKey

    @property
    def CachedAccessKey(self) -> str:
        """Get the cached access key."""
        return self.Element.CachedAccessKey

    @property
    def CachedAriaProperties(self) -> str:
        """Get the cached aria properties."""
        return self.Element.CachedAriaProperties

    @property
    def CachedAriaRole(self) -> str:
        """Get the cached aria role."""
        return self.Element.CachedAriaRole

    @property
    def CachedAutomationId(self) -> str:
        """Get the cached automation id."""
        return self.Element.CachedAutomationId

    @property
    def CachedBoundingRectangle(self) -> Rect:
        """Get the cached bounding rectangle."""
        rect = self.Element.CachedBoundingRectangle
        return Rect(rect.left, rect.top, rect.right, rect.bottom)

    @property
    def CachedClassName(self) -> str:
        """Get the cached class name."""
        return self.Element.CachedClassName

    @property
    def CachedControlType(self) -> int:
        """Get the cached control type."""
        return self.Element.CachedControlType

    @property
    def CachedControlTypeName(self) -> str:
        """Get the cached control type name."""
        try:
            return ControlTypeNames.get(self.CachedControlType, "Unknown")
        except Exception:
            return "Unknown"

    @property
    def CachedControllerFor(self) -> Any:
        """Get the cached controller for."""
        return self.Element.CachedControllerFor

    @property
    def CachedCulture(self) -> int:
        """Get the cached culture."""
        return self.Element.CachedCulture

    @property
    def CachedDescribedBy(self) -> Any:
        """Get the cached described by."""
        return self.Element.CachedDescribedBy

    @property
    def CachedFlowsTo(self) -> Any:
        """Get the cached flows to."""
        return self.Element.CachedFlowsTo

    @property
    def CachedFrameworkId(self) -> str:
        """Get the cached framework id."""
        return self.Element.CachedFrameworkId

    @property
    def CachedHasKeyboardFocus(self) -> bool:
        """Get the cached has keyboard focus."""
        return self.Element.CachedHasKeyboardFocus

    @property
    def CachedHeadingLevel(self) -> HeadingLevel:
        """
        Get the cached heading level, as a `HeadingLevel`.

        Unlike the neighbouring accessors this cannot read `Element.CachedHeadingLevel`:
        that member only exists on `IUIAutomationElement6` and later, while `Element` is a
        plain `IUIAutomationElement`. Going through `GetCachedPropertyValue` keeps it
        working regardless of the interface version comtypes generated.

        Requires `PropertyId.HeadingLevelProperty` in the `CacheRequest`.
        Return `HeadingLevel`, `HeadingLevel.None_` when the element is not a heading.
        """
        value = self.GetCachedPropertyValue(PropertyId.HeadingLevelProperty)
        try:
            return HeadingLevel(value)
        except ValueError:
            return HeadingLevel.None_

    @property
    def CachedIsDialog(self) -> bool:
        """
        Get whether the element is a dialog, from the cache.

        Read via `GetCachedPropertyValue` because `Element.CachedIsDialog` only exists on
        `IUIAutomationElement9` and later.

        Requires `PropertyId.IsDialogProperty` in the `CacheRequest`.
        """
        return bool(self.GetCachedPropertyValue(PropertyId.IsDialogProperty))

    @property
    def CachedLandmarkType(self) -> LandmarkType:
        """
        Get the cached landmark type, as a `LandmarkType`.

        Read via `GetCachedPropertyValue` because `Element.CachedLandmarkType` only exists
        on `IUIAutomationElement6` and later.

        Requires `PropertyId.LandmarkTypeProperty` in the `CacheRequest`.
        Return `LandmarkType`, `LandmarkType.None_` when the element is not a landmark.
        """
        value = self.GetCachedPropertyValue(PropertyId.LandmarkTypeProperty)
        try:
            return LandmarkType(value)
        except ValueError:
            return LandmarkType.None_

    @property
    def CachedHelpText(self) -> str:
        """Get the cached help text."""
        return self.Element.CachedHelpText

    @property
    def CachedIsContentElement(self) -> bool:
        """Get the cached is content element."""
        return self.Element.CachedIsContentElement

    @property
    def CachedIsControlElement(self) -> bool:
        """Get the cached is control element."""
        return self.Element.CachedIsControlElement

    @property
    def CachedIsDataValidForForm(self) -> bool:
        """Get the cached is data valid for form."""
        return self.Element.CachedIsDataValidForForm

    @property
    def CachedIsEnabled(self) -> bool:
        """Get the cached is enabled."""
        return self.Element.CachedIsEnabled

    @property
    def CachedIsKeyboardFocusable(self) -> bool:
        """Get the cached is keyboard focusable."""
        return self.Element.CachedIsKeyboardFocusable

    @property
    def CachedIsOffscreen(self) -> bool:
        """Get the cached is offscreen."""
        return self.Element.CachedIsOffscreen

    @property
    def CachedIsPassword(self) -> bool:
        """Get the cached is password."""
        return self.Element.CachedIsPassword

    @property
    def CachedIsRequiredForForm(self) -> bool:
        """Get the cached is required for form."""
        return self.Element.CachedIsRequiredForForm

    @property
    def CachedItemStatus(self) -> str:
        """Get the cached item status."""
        return self.Element.CachedItemStatus

    @property
    def CachedItemType(self) -> str:
        """Get the cached item type."""
        return self.Element.CachedItemType

    @property
    def CachedLabeledBy(self) -> Any:
        """Get the cached labeled by."""
        return self.Element.CachedLabeledBy

    @property
    def CachedLocalizedControlType(self) -> str:
        """Get the cached localized control type."""
        return self.Element.CachedLocalizedControlType

    @property
    def CachedName(self) -> str:
        """Get the cached name."""
        return self.Element.CachedName

    @property
    def CachedNativeWindowHandle(self) -> int:
        """Get the cached native window handle."""
        return self.Element.CachedNativeWindowHandle

    @property
    def CachedOrientation(self) -> int:
        """Get the cached orientation."""
        return self.Element.CachedOrientation

    @property
    def CachedProcessId(self) -> int:
        """Get the cached process id."""
        return self.Element.CachedProcessId

    @property
    def CachedProviderDescription(self) -> str:
        """Get the cached provider description."""
        return self.Element.CachedProviderDescription

    @property
    def AcceleratorKey(self) -> str:
        """
        Property AcceleratorKey.
        Call IUIAutomationElement::get_CurrentAcceleratorKey.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentacceleratorkey
        """
        return self.Element.CurrentAcceleratorKey

    @property
    def AccessKey(self) -> str:
        """
        Property AccessKey.
        Call IUIAutomationElement::get_CurrentAccessKey.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentaccesskey
        """
        return self.Element.CurrentAccessKey

    @property
    def AriaProperties(self) -> str:
        """
        Property AriaProperties.
        Call IUIAutomationElement::get_CurrentAriaProperties.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentariaproperties
        """
        return self.Element.CurrentAriaProperties

    @property
    def AriaRole(self) -> str:
        """
        Property AriaRole.
        Call IUIAutomationElement::get_CurrentAriaRole.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentariarole
        """
        return self.Element.CurrentAriaRole

    @property
    def AutomationId(self) -> str:
        """
        Property AutomationId.
        Call IUIAutomationElement::get_CurrentAutomationId.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentautomationid
        """
        return self.Element.CurrentAutomationId

    @property
    def BoundingRectangle(self) -> Rect:
        """
        Property BoundingRectangle.
        Call IUIAutomationElement::get_CurrentBoundingRectangle.
        Return `Rect`.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentboundingrectangle

        rect = control.BoundingRectangle
        print(rect.left, rect.top, rect.right, rect.bottom, rect.width(), rect.height(), rect.xcenter(), rect.ycenter())
        """
        rect = self.Element.CurrentBoundingRectangle
        return Rect(rect.left, rect.top, rect.right, rect.bottom)

    @property
    def ClassName(self) -> str:
        """
        Property ClassName.
        Call IUIAutomationElement::get_CurrentClassName.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentclassname
        """
        return self.Element.CurrentClassName

    @property
    def ControlType(self) -> int:
        """
        Property ControlType.
        Return int, a value in class `ControlType`.
        Call IUIAutomationElement::get_CurrentControlType.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentcontroltype
        """
        return self.Element.CurrentControlType

    # @property
    # def ControllerFor(self):
    # return self.Element.CurrentControllerFor

    @property
    def Culture(self) -> int:
        """
        Property Culture.
        Call IUIAutomationElement::get_CurrentCulture.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentculture
        """
        return self.Element.CurrentCulture

    # @property
    # def DescribedBy(self):
    # return self.Element.CurrentDescribedBy

    # @property
    # def FlowsTo(self):
    # return self.Element.CurrentFlowsTo

    @property
    def FrameworkId(self) -> str:
        """
        Property FrameworkId.
        Call IUIAutomationElement::get_CurrentFrameworkId.
        Return str, such as Win32, WPF...
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentframeworkid
        """
        return self.Element.CurrentFrameworkId

    @property
    def HasKeyboardFocus(self) -> bool:
        """
        Property HasKeyboardFocus.
        Call IUIAutomationElement::get_CurrentHasKeyboardFocus.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currenthaskeyboardfocus
        """
        return bool(self.Element.CurrentHasKeyboardFocus)

    @property
    def HelpText(self) -> str:
        """
        Property HelpText.
        Call IUIAutomationElement::get_CurrentHelpText.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currenthelptext
        """
        return self.Element.CurrentHelpText

    @property
    def IsContentElement(self) -> bool:
        """
        Property IsContentElement.
        Call IUIAutomationElement::get_CurrentIsContentElement.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentiscontentelement
        """
        return bool(self.Element.CurrentIsContentElement)

    @property
    def IsControlElement(self) -> bool:
        """
        Property IsControlElement.
        Call IUIAutomationElement::get_CurrentIsControlElement.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentiscontrolelement
        """
        return bool(self.Element.CurrentIsControlElement)

    @property
    def IsDataValidForForm(self) -> bool:
        """
        Property IsDataValidForForm.
        Call IUIAutomationElement::get_CurrentIsDataValidForForm.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentisdatavalidforform
        """
        return bool(self.Element.CurrentIsDataValidForForm)

    @property
    def IsEnabled(self) -> bool:
        """
        Property IsEnabled.
        Call IUIAutomationElement::get_CurrentIsEnabled.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentisenabled
        """
        return bool(self.Element.CurrentIsEnabled)

    @property
    def IsKeyboardFocusable(self) -> bool:
        """
        Property IsKeyboardFocusable.
        Call IUIAutomationElement::get_CurrentIsKeyboardFocusable.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentiskeyboardfocusable
        """
        return bool(self.Element.CurrentIsKeyboardFocusable)

    @property
    def IsOffscreen(self) -> bool:
        """
        Property IsOffscreen.
        Call IUIAutomationElement::get_CurrentIsOffscreen.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentisoffscreen
        """
        return bool(self.Element.CurrentIsOffscreen)

    @property
    def IsPassword(self) -> bool:
        """
        Property IsPassword.
        Call IUIAutomationElement::get_CurrentIsPassword.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentispassword
        """
        return bool(self.Element.CurrentIsPassword)

    @property
    def IsRequiredForForm(self) -> bool:
        """
        Property IsRequiredForForm.
        Call IUIAutomationElement::get_CurrentIsRequiredForForm.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentisrequiredforform
        """
        return bool(self.Element.CurrentIsRequiredForForm)

    @property
    def ItemStatus(self) -> str:
        """
        Property ItemStatus.
        Call IUIAutomationElement::get_CurrentItemStatus.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentitemstatus
        """
        return self.Element.CurrentItemStatus

    @property
    def ItemType(self) -> str:
        """
        Property ItemType.
        Call IUIAutomationElement::get_CurrentItemType.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentitemtype
        """
        return self.Element.CurrentItemType

    # @property
    # def LabeledBy(self):
    # return self.Element.CurrentLabeledBy

    @property
    def LocalizedControlType(self) -> str:
        """
        Property LocalizedControlType.
        Call IUIAutomationElement::get_CurrentLocalizedControlType.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentlocalizedcontroltype
        """
        return self.Element.CurrentLocalizedControlType

    @property
    def Name(self) -> str:
        """
        Property Name.
        Call IUIAutomationElement::get_CurrentName.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentname
        """
        return self.Element.CurrentName or ""  # CurrentName may be None

    @property
    def NativeWindowHandle(self) -> int:
        """
        Property NativeWindowHandle.
        Call IUIAutomationElement::get_CurrentNativeWindowHandle.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentnativewindowhandle
        """
        try:
            handle = self.Element.CurrentNativeWindowHandle
        except comtypes.COMError:
            return 0
        return 0 if handle is None else handle

    @property
    def Orientation(self) -> int:
        """
        Property Orientation.
        Return int, a value in class `OrientationType`.
        Call IUIAutomationElement::get_CurrentOrientation.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentorientation
        """
        return self.Element.CurrentOrientation

    @property
    def ProcessId(self) -> int:
        """
        Property ProcessId.
        Call IUIAutomationElement::get_CurrentProcessId.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentprocessid
        """
        return self.Element.CurrentProcessId

    @property
    def ProviderDescription(self) -> str:
        """
        Property ProviderDescription.
        Call IUIAutomationElement::get_CurrentProviderDescription.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-get_currentproviderdescription
        """
        return self.Element.CurrentProviderDescription

    def FindAll(self, scope: int, condition) -> List["Control"]:
        """
        Find all UI Automation elements that satisfy the specified condition.
        Call IUIAutomationElement::FindAll.

        scope: int, a value in class `TreeScope`, specifying the scope of the search.
        condition: a condition object from IUIAutomation.CreateTrueCondition() or similar.
        Return List[Control], a list of `Control` subclasses that match the condition.

        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-findall
        """
        elementArray = self.Element.FindAll(scope, condition)
        if not elementArray:
            return []

        controls = []
        length = elementArray.Length
        for i in range(length):
            element = elementArray.GetElement(i)
            control = Control.CreateControlFromElement(element)
            if control:
                controls.append(control)
        return controls

    def FindAllBuildCache(
        self, scope: int, condition, cacheRequest: "CacheRequest"
    ) -> List["Control"]:
        """
        Find all UI Automation elements that satisfy the specified condition, and cache properties and patterns.
        Call IUIAutomationElement::FindAllBuildCache.

        scope: int, a value in class `TreeScope`, specifying the scope of the search.
        condition: a condition object from IUIAutomation.CreateTrueCondition() or similar.
        cacheRequest: CacheRequest, specifies the properties and patterns to cache.
        Return List[Control], a list of `Control` subclasses that match the condition with cached data.

        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-findallbuildcache
        """
        elementArray = self.Element.FindAllBuildCache(scope, condition, cacheRequest.check_request)
        if not elementArray:
            return []

        controls = []
        length = elementArray.Length
        for i in range(length):
            element = elementArray.GetElement(i)
            control = Control.CreateControlFromElement(element)
            if control:
                controls.append(control)
        return controls

    def FindFirst(self, scope: int, condition) -> "Control" | None:
        """
        Find the first UI Automation element that satisfies the specified condition.
        Call IUIAutomationElement::FindFirst.

        scope: int, a value in class `TreeScope`, specifying the scope of the search.
        condition: a condition object from IUIAutomation.CreateTrueCondition() or similar.
        Return `Control` subclass or None.

        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-findfirst
        """
        element = self.Element.FindFirst(scope, condition)
        return Control.CreateControlFromElement(element)

    def FindFirstBuildCache(
        self, scope: int, condition, cacheRequest: "CacheRequest"
    ) -> "Control" | None:
        """
        Find the first UI Automation element that satisfies the specified condition, and cache properties and patterns.
        Call IUIAutomationElement::FindFirstBuildCache.

        scope: int, a value in class `TreeScope`, specifying the scope of the search.
        condition: a condition object from IUIAutomation.CreateTrueCondition() or similar.
        cacheRequest: CacheRequest, specifies the properties and patterns to cache.
        Return `Control` subclass or None with cached data.

        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-findfirstbuildcache
        """
        element = self.Element.FindFirstBuildCache(scope, condition, cacheRequest.check_request)
        return Control.CreateControlFromElement(element)

    def GetCachedChildren(self) -> List["Control"]:
        """
        Retrieve the cached child elements of this UI Automation element.
        Call IUIAutomationElement::GetCachedChildren.

        Return List[Control], a list of cached child `Control` subclasses.
        Note: Children are cached only if the scope of the cache request included TreeScope_Subtree,
        TreeScope_Children, or TreeScope_Descendants.

        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getcachedchildren
        """
        try:
            elementArray = self.Element.GetCachedChildren()
            if not elementArray:
                return []

            controls = []
            length = elementArray.Length
            for i in range(length):
                element = elementArray.GetElement(i)
                control = Control.CreateControlFromElement(element)
                if control:
                    controls.append(control)
            return controls
        except comtypes.COMError:
            return []

    def GetCachedParent(self) -> "Control" | None:
        """
        Retrieve the cached parent of this UI Automation element.
        Call IUIAutomationElement::GetCachedParent.

        Return `Control` subclass or None.

        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getcachedparent
        """
        try:
            element = self.Element.GetCachedParent()
            return Control.CreateControlFromElement(element)
        except comtypes.COMError:
            return None

    def GetCachedPatternAs(self, patternId: int, riid):
        """
        Retrieve a cached pattern interface from this UI Automation element, with a specific interface ID.
        Call IUIAutomationElement::GetCachedPatternAs.

        patternId: int, a value in class `PatternId`.
        riid: GUID, the interface identifier.
        Return a pattern object if the pattern was cached, else None.

        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getcachedpatternas
        """
        try:
            return self.Element.GetCachedPatternAs(patternId, riid)
        except comtypes.COMError:
            return None

    def GetCachedPropertyValue(self, propertyId: int) -> Any:
        """
        Retrieve a cached property value from this UI Automation element.
        Call IUIAutomationElement::GetCachedPropertyValue.

        propertyId: int, a value in class `PropertyId`.
        Return Any, the cached property value corresponding to propertyId.

        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getcachedpropertyvalue
        """
        try:
            return self.Element.GetCachedPropertyValue(propertyId)
        except comtypes.COMError:
            return None

    def GetCachedPropertyValueEx(self, propertyId: int, ignoreDefaultValue: int) -> Any:
        """
        Retrieve a cached property value from this UI Automation element, optionally ignoring the default value.
        Call IUIAutomationElement::GetCachedPropertyValueEx.

        propertyId: int, a value in class `PropertyId`.
        ignoreDefaultValue: int, 0 or 1. If 1, a default value is not returned.
        Return Any, the cached property value corresponding to propertyId.

        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getcachedpropertyvalueex
        """
        try:
            return self.Element.GetCachedPropertyValueEx(propertyId, ignoreDefaultValue)
        except comtypes.COMError:
            return None

    def GetClickablePoint(self) -> Tuple[int, int, bool]:
        """
        Call IUIAutomationElement::GetClickablePoint.
        Return Tuple[int, int, bool], three items tuple (x, y, gotClickable), such as (20, 10, True)
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getclickablepoint
        """
        point, gotClickable = self.Element.GetClickablePoint()
        return (point.x, point.y, bool(gotClickable))

    def GetPattern(self, patternId: int):
        """
        Call IUIAutomationElement::GetCurrentPattern.
        Get a new pattern by pattern id if it supports the pattern.
        patternId: int, a value in class `PatternId`.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getcurrentpattern
        """
        try:
            pattern = self.Element.GetCurrentPattern(patternId)
            if pattern:
                subPattern = CreatePattern(patternId, pattern)
                self._supportedPatterns[patternId] = subPattern
                return subPattern
        except comtypes.COMError:
            pass

    def GetPatternAs(self, patternId: int, riid):
        """
        Call IUIAutomationElement::GetCurrentPatternAs.
        Get a new pattern by pattern id if it supports the pattern, todo.
        patternId: int, a value in class `PatternId`.
        riid: GUID.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getcurrentpatternas
        """
        return self.Element.GetCurrentPatternAs(patternId, riid)

    def GetPropertyValue(self, propertyId: int) -> Any:
        """
        Call IUIAutomationElement::GetCurrentPropertyValue.
        propertyId: int, a value in class `PropertyId`.
        Return Any, corresponding type according to propertyId.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getcurrentpropertyvalue
        """
        return self.Element.GetCurrentPropertyValue(propertyId)

    def GetWordBoundingBox(
        self, word: str, backward: bool = False, ignoreCase: bool = True
    ) -> List[Rect] | None:
        """
        Find `word` in the control's text (via `TextPattern`) and return its bounding box(es).
        word: str, the word/substring to search for.
        backward: bool, search from the end of the document backward.
        ignoreCase: bool, case-insensitive search.
        Return List[Rect] or None if the control has no `TextPattern` or the word wasn't found.
            Usually a single `Rect`; more than one if the match wraps across lines.
        """
        textPattern = self.GetPattern(PatternId.TextPattern)
        if textPattern is None:
            return None
        wordRange = textPattern.DocumentRange.FindText(word, backward, ignoreCase)
        if wordRange is None:
            return None
        return wordRange.GetBoundingRectangles()

    def GetWordBoundingBoxAtPoint(self, x: int, y: int) -> List[Rect] | None:
        """
        Return the bounding box of the word at screen point (x, y), via `TextPattern`.
        Return List[Rect] or None if the control has no `TextPattern` or there is no text at the point.
        """
        textPattern = self.GetPattern(PatternId.TextPattern)
        if textPattern is None:
            return None
        pointRange = textPattern.RangeFromPoint(x, y)
        if pointRange is None:
            return None
        pointRange.ExpandToEnclosingUnit(TextUnit.Word)
        return pointRange.GetBoundingRectangles()

    def GetWordFontSize(self, word: str, backward: bool = False, ignoreCase: bool = True) -> float | None:
        """
        Find `word` in the control's text (via `TextPattern`) and return its font size in points.
        Return float or None if the control has no `TextPattern`, the word wasn't found,
            or the attribute is mixed/unsupported (IUIAutomationTextRange::GetAttributeValue
            returns UiaGetReservedMixedAttributeValue in that case).
        """
        textPattern = self.GetPattern(PatternId.TextPattern)
        if textPattern is None:
            return None
        wordRange = textPattern.DocumentRange.FindText(word, backward, ignoreCase)
        if wordRange is None:
            return None
        value = wordRange.GetAttributeValue(TextAttributeId.FontSizeAttribute)
        return float(value) if isinstance(value, (int, float)) else None

    def _iter_word_ranges(self, textPattern) -> Generator[Tuple[str, "TextRange"], None, None]:
        """
        Foundation for every word-level `TextPattern` feature (bounding boxes, attributes,
        selection): yields (word_text, word_range) for each word in the control's visible text.
        Walks each visible range word-by-word via `TextRange.Move(TextUnit.Word, 1)`.

        waitTime=0 on `ExpandToEnclosingUnit`/`Move`: these are read-only navigation calls, not
        UI-mutating actions — the default 0.5s settling delay only makes sense for things like
        Click/Toggle, and was previously the dominant cost of walking a control's words.

        The yielded `word_range` is a single mutable COM range reused across iterations (moved
        forward each time) -- callers must fully consume it (read text/rects/attributes) before
        requesting the next word, never retain it past that iteration.
        """
        for visibleRange in textPattern.GetVisibleRanges():
            wordRange = visibleRange.Clone()
            wordRange.ExpandToEnclosingUnit(TextUnit.Word, waitTime=0)
            while wordRange.CompareEndpoints(
                TextPatternRangeEndpoint.Start, visibleRange, TextPatternRangeEndpoint.End
            ) < 0:
                text = wordRange.GetText(-1).strip()
                if text:
                    yield text, wordRange
                moved = wordRange.Move(TextUnit.Word, 1, waitTime=0)
                if moved == 0:
                    break

    def GetAllWordBoundingBoxes(self) -> List[Tuple[str, List[Rect]]] | None:
        """
        Return (word, bounding boxes) for every word in the control's visible text (via `TextPattern`).
        Return List[Tuple[str, List[Rect]]] or None if the control has no `TextPattern`.
            Bounding boxes is usually a single `Rect`; more than one if the word wraps across lines.
            Boxes are vertically shrunk to the glyph's font-size (in place of the raw
            line-height rect `GetBoundingRectangles` returns) when the font size attribute
            is available; otherwise the untouched line-height rect is kept.
        """
        textPattern = self.GetPattern(PatternId.TextPattern)
        if textPattern is None:
            return None
        dpi = _get_system_dpi() or 96
        # Font size is virtually always uniform across a control — look it up once on the
        # whole document instead of once per word (each TextRange COM call is expensive).
        doc_font_size = textPattern.DocumentRange.GetAttributeValue(TextAttributeId.FontSizeAttribute)
        uniform_font_size = doc_font_size if isinstance(doc_font_size, (int, float)) else None
        words: List[Tuple[str, List[Rect]]] = []
        for text, wordRange in self._iter_word_ranges(textPattern):
            rects = wordRange.GetBoundingRectangles()
            font_size = uniform_font_size
            if font_size is None:
                attr = wordRange.GetAttributeValue(TextAttributeId.FontSizeAttribute)
                font_size = attr if isinstance(attr, (int, float)) else None
            if font_size is not None:
                rects = [self._shrink_rect_to_font_size(rect, font_size, dpi) for rect in rects]
            words.append((text, rects))
        return words

    def GetWordAttributes(
        self, attribute_ids: List[int], uniform_attribute_ids: Optional[set[int]] = None
    ) -> List[Tuple[str, List[Rect], Dict[int, Any]]] | None:
        """
        Return (word, bounding boxes, {attribute_id: value}) for every word in the control's
        visible text, for each id in `attribute_ids` (values from `TextAttributeId`).
        Return None if the control has no `TextPattern`.

        uniform_attribute_ids: subset of `attribute_ids` you expect to be constant across the
            whole control (e.g. `FontSizeAttribute`, `FontNameAttribute`) — each of those is
            looked up once on `DocumentRange` instead of once per word. Attributes that
            legitimately vary per word (colors, bold/italic, hyperlinks) must NOT be listed
            here or every word will silently get the document's single value.
            If a "uniform" attribute turns out mixed (`GetAttributeValue` returns the
            UiaGetReservedMixedAttributeValue sentinel, i.e. not a plain int/float/str), it
            falls back to a per-word lookup for that attribute automatically.

        Keep `attribute_ids` short for non-uniform attributes: each one adds a COM call per word.
        """
        textPattern = self.GetPattern(PatternId.TextPattern)
        if textPattern is None:
            return None
        uniform_attribute_ids = uniform_attribute_ids or set()

        uniform_values: Dict[int, Any] = {}
        for attribute_id in attribute_ids:
            if attribute_id not in uniform_attribute_ids:
                continue
            value = textPattern.DocumentRange.GetAttributeValue(attribute_id)
            if isinstance(value, (int, float, str)):
                uniform_values[attribute_id] = value

        # Whatever isn't resolved as document-uniform (either never marked as such, or marked
        # but the value turned out mixed) is looked up per-word -- but batched into a single
        # `GetAttributeValues` COM call per word instead of one `GetAttributeValue` call per
        # attribute per word.
        per_word_ids = [attribute_id for attribute_id in attribute_ids if attribute_id not in uniform_values]

        results: List[Tuple[str, List[Rect], Dict[int, Any]]] = []
        for text, wordRange in self._iter_word_ranges(textPattern):
            rects = wordRange.GetBoundingRectangles()
            attrs: Dict[int, Any] = dict(uniform_values)
            if per_word_ids:
                batched = wordRange.GetAttributeValues(per_word_ids)
                if batched is not None:
                    attrs.update(zip(per_word_ids, batched))
                else:
                    # Provider doesn't implement IUIAutomationTextRange3 -- fall back to one
                    # GetAttributeValue call per attribute.
                    for attribute_id in per_word_ids:
                        attrs[attribute_id] = wordRange.GetAttributeValue(attribute_id)
            results.append((text, rects, attrs))
        return results

    def GetWordHyperlinks(self) -> List[Tuple[str, Rect, str]]:
        """
        Return (link text, bounding box, url) for every hyperlink under this control.

        NOTE: `TextAttributeId.LinkAttribute` looks like the natural source for this but is
        NOT viable in practice -- verified against real Edge/Chromium hyperlinks, it always
        returns the shared `UiaGetReservedNotSupportedValue` sentinel (same pointer for every
        word, link or not), never an actual URL. Chromium simply doesn't implement that text
        attribute. Instead, browsers expose each hyperlink as its own `HyperlinkControl`
        element in the tree, with the URL readable via `LegacyIAccessibleValueProperty` -- this
        walks descendants for that control type instead of touching `TextPattern` at all.
        Return [] if there are no hyperlink descendants (never None -- this doesn't depend on
        `TextPattern` support).
        """
        condition = CreatePropertyCondition(PropertyId.ControlTypeProperty, ControlType.HyperlinkControl)
        hyperlinks: List[Tuple[str, Rect, str]] = []
        for link_control in self.FindAll(TreeScope.TreeScope_Descendants, condition):
            try:
                url = link_control.GetPropertyValue(PropertyId.LegacyIAccessibleValueProperty)
                box = link_control.BoundingRectangle
                name = link_control.Name
            except Exception:
                continue
            if isinstance(url, str) and url and box.width() > 0 and box.height() > 0:
                hyperlinks.append((name, box, url))
        return hyperlinks

    def SelectWord(self, word: str, backward: bool = False, ignoreCase: bool = True) -> bool:
        """
        Find `word` in the control's text (via `TextPattern`) and select it, replacing any
        existing selection (`IUIAutomationTextRange::Select`).
        word: str, the word/substring to search for.
        backward: bool, search from the end of the document backward.
        ignoreCase: bool, case-insensitive search.
        Return bool, True if the word was found and selected, False if the control has no
            `TextPattern` or the word wasn't found.
        """
        textPattern = self.GetPattern(PatternId.TextPattern)
        if textPattern is None:
            return False
        wordRange = textPattern.DocumentRange.FindText(word, backward, ignoreCase)
        if wordRange is None:
            return False
        return wordRange.Select(waitTime=0)

    @staticmethod
    def _shrink_rect_to_font_size(rect: Rect, font_size_pt: float, dpi: int) -> Rect:
        """
        Shrink `rect`'s height to the pixel height implied by `font_size_pt` (points), anchored
        to the rect's bottom edge. `GetBoundingRectangles` reports the control's full line-height
        box, which is noticeably taller than the glyphs themselves; the bottom edge tracks the
        line's baseline/descent closely, while the extra leading is padded above it.
        """
        font_px = font_size_pt * dpi / 72.0
        if font_px <= 0 or font_px >= rect.height():
            return rect
        return Rect(
            left=rect.left,
            top=int(round(rect.bottom - font_px)),
            right=rect.right,
            bottom=rect.bottom,
        )

    def GetPropertyValueEx(self, propertyId: int, ignoreDefaultValue: int) -> Any:
        """
        Call IUIAutomationElement::GetCurrentPropertyValueEx.
        propertyId: int, a value in class `PropertyId`.
        ignoreDefaultValue: int, 0 or 1.
        Return Any, corresponding type according to propertyId.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getcurrentpropertyvalueex
        """
        return self.Element.GetCurrentPropertyValueEx(propertyId, ignoreDefaultValue)

    def GetAllPropertyValues(
        self,
        cached: bool = False,
        includeUnsupported: bool = False,
        resolveElements: bool = True,
        propertyIds: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """
        Read every UI Automation property this element exposes, keyed by property name.

        Unsupported properties are the norm, not the exception -- a typical element
        supports ~30 of the ~175 defined properties. They are resolved with
        `GetCurrentPropertyValueEx(propertyId, ignoreDefaultValue=1)` so that a provider
        that does not implement a property yields the reserved "not supported" sentinel
        instead of a *type default* that is indistinguishable from a real value. Using
        plain `GetCurrentPropertyValue` here would be actively misleading: a plain Pane
        reports `ToggleToggleState=2` and `GridItemRowSpan=1` despite supporting neither
        pattern.

        cached: bool, read from the element's cache instead of live. Only properties
            added to the `CacheRequest` used to build this element are available; the
            rest are reported as not supported. Cached reads cannot use the `Ex` variant,
            so defaults cannot be filtered out -- prefer live reads when you need to know
            what is genuinely supported.
        includeUnsupported: bool, if True, unsupported properties are included with the
            value `PROPERTY_NOT_SUPPORTED`. If False (default) they are omitted.
        resolveElements: bool, if True, element-valued and element-array-valued
            properties are converted to `Control` / List[`Control`]. If False they are
            left as the raw `POINTER(IUnknown)`.
        propertyIds: optional list of `PropertyId` values to restrict the query to.
            Defaults to every property in `PropertyIdNames`.

        Return Dict[str, Any], mapping property name (e.g. 'NameProperty') to value.

        NOTE: each property is a separate cross-process COM call, so a full live read is
        ~175 round-trips (tens of milliseconds). This is a diagnostic/introspection API
        -- do not call it per node during a tree traversal; add the handful of properties
        you need to a `CacheRequest` instead.
        """
        ids = propertyIds if propertyIds is not None else list(PropertyIdNames.keys())
        notSupported = _get_reserved_not_supported_address()
        values: Dict[str, Any] = {}
        for propertyId in ids:
            name = PropertyIdNames.get(propertyId, str(propertyId))
            try:
                if cached:
                    value = self.Element.GetCachedPropertyValue(propertyId)
                else:
                    value = self.Element.GetCurrentPropertyValueEx(propertyId, 1)
            except (COMError, ValueError):
                if includeUnsupported:
                    values[name] = PROPERTY_NOT_SUPPORTED
                continue

            if _is_reserved_value(value, notSupported):
                if includeUnsupported:
                    values[name] = PROPERTY_NOT_SUPPORTED
                continue

            values[name] = _normalize_property_value(propertyId, value, resolveElements)
        return values

    def GetSupportedPropertyNames(self) -> List[str]:
        """
        Return List[str], the names of the UI Automation properties this element actually
        implements, sorted alphabetically. Thin wrapper over `GetAllPropertyValues`.
        """
        return sorted(self.GetAllPropertyValues().keys())

    def GetRuntimeId(self) -> List[int]:
        """
        Call IUIAutomationElement::GetRuntimeId.
        Return List[int], a list of int.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getruntimeid
        """
        return self.Element.GetRuntimeId()

    # QueryInterface
    # Release

    def SetFocus(self) -> bool:
        """
        Call IUIAutomationElement::SetFocus.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-setfocus
        """
        try:
            return self.Element.SetFocus() == S_OK
        except comtypes.COMError:
            return False

    @property
    def Element(self):
        """
        Property Element.
        Return `ctypes.POINTER(IUIAutomationElement)`.
        """
        if not self._element:
            self.Refind(
                maxSearchSeconds=TIME_OUT_SECOND,
                searchIntervalSeconds=self.searchInterval,
            )
        return self._element

    @property
    def ControlTypeName(self) -> str:
        """
        Property ControlTypeName.
        """
        return ControlTypeNames[self.ControlType]

    def GetCachedPattern(self, patternId: int, cache: bool):
        """
        Get a pattern by patternId.
        patternId: int, a value in class `PatternId`.
        Return a pattern if it supports the pattern else None.
        cache: bool, if True, store the pattern for later use, if False, get a new pattern by `self.GetPattern`.
        """
        if cache:
            pattern = self._supportedPatterns.get(patternId, None)
            if pattern:
                return pattern
            else:
                pattern = self.GetPattern(patternId)
                if pattern:
                    self._supportedPatterns[patternId] = pattern
                    return pattern
        else:
            pattern = self.GetPattern(patternId)
            if pattern:
                self._supportedPatterns[patternId] = pattern
                return pattern

    def GetLegacyIAccessiblePattern(self) -> LegacyIAccessiblePattern:
        """
        Return `LegacyIAccessiblePattern` if it supports the pattern else None.
        """
        return self.GetPattern(PatternId.LegacyIAccessiblePattern)

    def GetAncestorControl(self, condition: Callable[["Control", int], bool]) -> "Control" | None:
        """
        Get an ancestor control that matches the condition.
        condition: Callable[[Control, int], bool], function(control: Control, depth: int) -> bool,
                   depth starts with -1 and decreses when search goes up.
        Return `Control` subclass or None.
        """
        ancestor = self
        depth = 0
        while ancestor is not None:
            ancestor = ancestor.GetParentControl()
            depth -= 1
            if ancestor:
                if condition(ancestor, depth):
                    return ancestor
        return None

    def GetParentControl(self) -> "Control" | None:
        """
        Return `Control` subclass or None.
        """
        ele = _AutomationClient.instance().ViewWalker.GetParentElement(self.Element)
        return Control.CreateControlFromElement(ele)

    def GetFirstChildControl(self) -> "Control" | None:
        """
        Return `Control` subclass or None.
        """
        ele = _AutomationClient.instance().ViewWalker.GetFirstChildElement(self.Element)
        return Control.CreateControlFromElement(ele)

    def GetLastChildControl(self) -> "Control" | None:
        """
        Return `Control` subclass or None.
        """
        ele = _AutomationClient.instance().ViewWalker.GetLastChildElement(self.Element)
        return Control.CreateControlFromElement(ele)

    def GetNextSiblingControl(self) -> "Control" | None:
        """
        Return `Control` subclass or None.
        """
        ele = _AutomationClient.instance().ViewWalker.GetNextSiblingElement(self.Element)
        return Control.CreateControlFromElement(ele)

    def GetPreviousSiblingControl(self) -> "Control" | None:
        """
        Return `Control` subclass or None.
        """
        ele = _AutomationClient.instance().ViewWalker.GetPreviousSiblingElement(self.Element)
        return Control.CreateControlFromElement(ele)

    def GetSiblingControl(
        self, condition: Callable[["Control"], bool], forward: bool = True
    ) -> "Control" | None:
        """
        Get a sibling control that matches the condition.
        forward: bool, if True, only search next siblings, if False, search previous siblings first, then search next siblings.
        condition: Callable[[Control], bool], function(control: Control) -> bool.
        Return `Control` subclass or None.
        """
        if not forward:
            prev = self
            while True:
                prev = prev.GetPreviousSiblingControl()
                if prev:
                    if condition(prev):
                        return prev
                else:
                    break
        next_ = self
        while True:
            next_ = next_.GetNextSiblingControl()
            if next_:
                if condition(next_):
                    return next_
            else:
                break

    def GetChildren(self) -> List["Control"]:
        """
        Return List[Control], a list of `Control` subclasses.
        """
        if _ViewWalkerState.is_broken():
            return self._FindChildren()
        children = []
        child = self.GetFirstChildControl()
        while child:
            children.append(child)
            child = child.GetNextSiblingControl()
        if not children and _ViewWalkerState.should_probe():
            # The RawViewWalker returns nothing for every element on some
            # configurations (observed on Windows ARM64 running x86-emulated
            # Python), while FindAll on the same element returns the children
            # normally. Cross-check with FindAll the first few times a walk
            # comes back empty, and latch onto whichever path works so the
            # steady-state cost is one walk per element either way.
            children = self._FindChildren()
            _ViewWalkerState.record_probe(walker_is_broken=bool(children))
        return children

    def _FindChildren(self) -> List["Control"]:
        """
        Enumerate direct children via IUIAutomationElement::FindAll instead of
        the tree walker. Used as the fallback path when the walker is broken.

        Return List[Control], a list of `Control` subclasses.
        """
        try:
            return self.FindAll(TreeScope.TreeScope_Children, CreateTrueCondition())
        except COMError:
            return []

    def _CompareFunction(self, control: "Control", depth: int) -> bool:
        """
        Define how to search.
        control: `Control` or its subclass.
        depth: int, tree depth from searchFromControl.
        Return bool.
        """
        compareFunc = None
        for key, value in self.searchProperties.items():
            if "ControlType" == key:
                if value != control.ControlType:
                    return False
            elif "ClassName" == key:
                if value != control.ClassName:
                    return False
            elif "AutomationId" == key:
                if value != control.AutomationId:
                    return False
            elif "Depth" == key:
                if value != depth:
                    return False
            elif "Name" == key:
                if value != control.Name:
                    return False
            elif "SubName" == key:
                if value not in control.Name:
                    return False
            elif "RegexName" == key:
                if not self.regexName.match(control.Name):
                    return False
            elif "Compare" == key:
                compareFunc = value
        # use Compare at last
        if compareFunc and not compareFunc(control, depth):
            return False
        return True

    def Exists(
        self,
        maxSearchSeconds: float = 5,
        searchIntervalSeconds: float = SEARCH_INTERVAL,
        printIfNotExist: bool = False,
    ) -> bool:
        """
        maxSearchSeconds: float
        searchIntervalSeconds: float
        Find control every searchIntervalSeconds seconds in maxSearchSeconds seconds.
        Return bool, True if find
        """
        if self._element and self._elementDirectAssign:
            # if element is directly assigned, not by searching, just check whether self._element is valid
            # but I can't find an API in UIAutomation that can directly check
            rootElement = _AutomationClient.instance().IUIAutomation.GetRootElement()
            if _AutomationClient.instance().IUIAutomation.CompareElements(
                self._element, rootElement
            ):
                return True
            else:
                parentElement = _AutomationClient.instance().ViewWalker.GetParentElement(
                    self._element
                )
                if parentElement:
                    return True
                else:
                    return False
        # find the element
        if not self.searchProperties:
            raise LookupError("control's searchProperties must not be empty!")
        self._element = None
        startTime = ProcessTime()
        # Use same timeout(s) parameters for resolve all parents
        prev = self.searchFromControl
        if prev and not prev._element and not prev.Exists(maxSearchSeconds, searchIntervalSeconds):
            if printIfNotExist or DEBUG_EXIST_DISAPPEAR:
                pass
            return False
        startTime2 = ProcessTime()
        if DEBUG_SEARCH_TIME:
            startDateTime = datetime.datetime.now()
        while True:
            control = FindControl(
                self.searchFromControl,
                self._CompareFunction,
                self.searchDepth,
                False,
                self.foundIndex,
            )
            if control:
                self._element = control.Element
                control._element = 0  # control will be destroyed, but the element needs to be stored in self._element
                if DEBUG_SEARCH_TIME:
                    pass
                return True
            else:
                remain = startTime + maxSearchSeconds - ProcessTime()
                if remain > 0:
                    time.sleep(min(remain, searchIntervalSeconds))
                else:
                    if printIfNotExist or DEBUG_EXIST_DISAPPEAR:
                        pass
                    return False

    def Disappears(
        self,
        maxSearchSeconds: float = 5,
        searchIntervalSeconds: float = SEARCH_INTERVAL,
        printIfNotDisappear: bool = False,
    ) -> bool:
        """
        maxSearchSeconds: float
        searchIntervalSeconds: float
        Check if control disappears every searchIntervalSeconds seconds in maxSearchSeconds seconds.
        Return bool, True if control disappears.
        """
        global DEBUG_EXIST_DISAPPEAR
        start = ProcessTime()
        while True:
            temp = DEBUG_EXIST_DISAPPEAR
            DEBUG_EXIST_DISAPPEAR = False  # do not print for Exists
            if not self.Exists(0, 0, False):
                DEBUG_EXIST_DISAPPEAR = temp
                return True
            DEBUG_EXIST_DISAPPEAR = temp
            remain = start + maxSearchSeconds - ProcessTime()
            if remain > 0:
                time.sleep(min(remain, searchIntervalSeconds))
            else:
                if printIfNotDisappear or DEBUG_EXIST_DISAPPEAR:
                    pass
                return False

    def Refind(
        self,
        maxSearchSeconds: float = TIME_OUT_SECOND,
        searchIntervalSeconds: float = SEARCH_INTERVAL,
        raiseException: bool = True,
    ) -> bool:
        """
        Refind the control every searchIntervalSeconds seconds in maxSearchSeconds seconds.
        maxSearchSeconds: float.
        searchIntervalSeconds: float.
        raiseException: bool, if True, raise a LookupError if timeout.
        Return bool, True if find.
        """
        if not self.Exists(
            maxSearchSeconds,
            searchIntervalSeconds,
            False if raiseException else DEBUG_EXIST_DISAPPEAR,
        ):
            if raiseException:
                raise LookupError(
                    "Find Control Timeout({}s): {}".format(
                        maxSearchSeconds, self.GetSearchPropertiesStr()
                    )
                )
            else:
                return False
        return True

    def GetPosition(self, ratioX: float = 0.5, ratioY: float = 0.5) -> Tuple[int, int] | None:
        """
        Gets the position of the center of the control.
        ratioX: float.
        ratioY: float.
        Return Tuple[int, int], two ints tuple (x, y), the cursor position relative to screen(0, 0)
        """
        rect = self.BoundingRectangle
        if rect.width() == 0 or rect.height() == 0:
            return None
        x = rect.left + int(rect.width() * ratioX)
        y = rect.top + int(rect.height() * ratioY)
        return x, y

    def MoveCursorToInnerPos(
        self,
        x: int | None = None,
        y: int | None = None,
        ratioX: float = 0.5,
        ratioY: float = 0.5,
        simulateMove: bool = True,
    ) -> Tuple[int, int] | None:
        """
        Move cursor to control's internal position, default to center.
        x: int, if < 0, move to self.BoundingRectangle.right + x, if not None, ignore ratioX.
        y: int, if < 0, move to self.BoundingRectangle.bottom + y, if not None, ignore ratioY.
        ratioX: float.
        ratioY: float.
        simulateMove: bool.
        Return Tuple[int, int], two ints tuple (x, y), the cursor position relative to screen(0, 0)
            after moving or None if control's width or height is 0.
        """
        rect = self.BoundingRectangle
        if rect.width() == 0 or rect.height() == 0:
            return None
        if x is None:
            x = rect.left + int(rect.width() * ratioX)
        else:
            x = (rect.left if x >= 0 else rect.right) + x
        if y is None:
            y = rect.top + int(rect.height() * ratioY)
        else:
            y = (rect.top if y >= 0 else rect.bottom) + y
        if simulateMove and MAX_MOVE_SECOND > 0:
            MoveTo(x, y, waitTime=0)
        else:
            SetCursorPos(x, y)
        return x, y

    def MoveCursorToMyCenter(self, simulateMove: bool = True) -> Tuple[int, int] | None:
        """
        Move cursor to control's center.
        Return Tuple[int, int], two ints tuple (x, y), the cursor position relative to screen(0, 0) after moving.
        """
        return self.MoveCursorToInnerPos(simulateMove=simulateMove)

    def Click(
        self,
        x: int | None = None,
        y: int | None = None,
        ratioX: float = 0.5,
        ratioY: float = 0.5,
        simulateMove: bool = True,
        waitTime: float = OPERATION_WAIT_TIME,
    ) -> None:
        """
        x: int, if < 0, click self.BoundingRectangle.right + x, if not None, ignore ratioX.
        y: int, if < 0, click self.BoundingRectangle.bottom + y, if not None, ignore ratioY.
        ratioX: float.
        ratioY: float.
        simulateMove: bool, if True, first move cursor to control smoothly.
        waitTime: float.

        Click(), Click(ratioX=0.5, ratioY=0.5): click center.
        Click(10, 10): click left+10, top+10.
        Click(-10, -10): click right-10, bottom-10.
        """
        point = self.MoveCursorToInnerPos(x, y, ratioX, ratioY, simulateMove)
        if point:
            Click(point[0], point[1], waitTime)

    def MiddleClick(
        self,
        x: int | None = None,
        y: int | None = None,
        ratioX: float = 0.5,
        ratioY: float = 0.5,
        simulateMove: bool = True,
        waitTime: float = OPERATION_WAIT_TIME,
    ) -> None:
        """
        x: int, if < 0, middle click self.BoundingRectangle.right + x, if not None, ignore ratioX.
        y: int, if < 0, middle click self.BoundingRectangle.bottom + y, if not None, ignore ratioY.
        ratioX: float.
        ratioY: float.
        simulateMove: bool, if True, first move cursor to control smoothly.
        waitTime: float.

        MiddleClick(), MiddleClick(ratioX=0.5, ratioY=0.5): middle click center.
        MiddleClick(10, 10): middle click left+10, top+10.
        MiddleClick(-10, -10): middle click right-10, bottom-10.
        """
        point = self.MoveCursorToInnerPos(x, y, ratioX, ratioY, simulateMove)
        if point:
            MiddleClick(point[0], point[1], waitTime)

    def RightClick(
        self,
        x: int | None = None,
        y: int | None = None,
        ratioX: float = 0.5,
        ratioY: float = 0.5,
        simulateMove: bool = True,
        waitTime: float = OPERATION_WAIT_TIME,
    ) -> None:
        """
        x: int, if < 0, right click self.BoundingRectangle.right + x, if not None, ignore ratioX.
        y: int, if < 0, right click self.BoundingRectangle.bottom + y, if not None, ignore ratioY.
        ratioX: float.
        ratioY: float.
        simulateMove: bool, if True, first move cursor to control smoothly.
        waitTime: float.

        RightClick(), RightClick(ratioX=0.5, ratioY=0.5): right click center.
        RightClick(10, 10): right click left+10, top+10.
        RightClick(-10, -10): right click right-10, bottom-10.
        """
        point = self.MoveCursorToInnerPos(x, y, ratioX, ratioY, simulateMove)
        if point:
            RightClick(point[0], point[1], waitTime)

    def DoubleClick(
        self,
        x: int | None = None,
        y: int | None = None,
        ratioX: float = 0.5,
        ratioY: float = 0.5,
        simulateMove: bool = True,
        waitTime: float = OPERATION_WAIT_TIME,
    ) -> None:
        """
        x: int, if < 0, right click self.BoundingRectangle.right + x, if not None, ignore ratioX.
        y: int, if < 0, right click self.BoundingRectangle.bottom + y, if not None, ignore ratioY.
        ratioX: float.
        ratioY: float.
        simulateMove: bool, if True, first move cursor to control smoothly.
        waitTime: float.

        DoubleClick(), DoubleClick(ratioX=0.5, ratioY=0.5): double click center.
        DoubleClick(10, 10): double click left+10, top+10.
        DoubleClick(-10, -10): double click right-10, bottom-10.
        """
        x, y = self.MoveCursorToInnerPos(x, y, ratioX, ratioY, simulateMove)
        Click(x, y, GetDoubleClickTime() * 1.0 / 2000)
        Click(x, y, waitTime)

    def DragDrop(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        moveSpeed: float = 1,
        waitTime: float = OPERATION_WAIT_TIME,
    ) -> None:
        rect = self.BoundingRectangle
        if rect.width() == 0 or rect.height() == 0:
            return
        x1 = (rect.left if x1 >= 0 else rect.right) + x1
        y1 = (rect.top if y1 >= 0 else rect.bottom) + y1
        x2 = (rect.left if x2 >= 0 else rect.right) + x2
        y2 = (rect.top if y2 >= 0 else rect.bottom) + y2
        DragDrop(x1, y1, x2, y2, moveSpeed, waitTime)

    def RightDragDrop(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        moveSpeed: float = 1,
        waitTime: float = OPERATION_WAIT_TIME,
    ) -> None:
        rect = self.BoundingRectangle
        if rect.width() == 0 or rect.height() == 0:
            return
        x1 = (rect.left if x1 >= 0 else rect.right) + x1
        y1 = (rect.top if y1 >= 0 else rect.bottom) + y1
        x2 = (rect.left if x2 >= 0 else rect.right) + x2
        y2 = (rect.top if y2 >= 0 else rect.bottom) + y2
        RightDragDrop(x1, y1, x2, y2, moveSpeed, waitTime)

    def WheelDown(
        self,
        x: int | None = None,
        y: int | None = None,
        ratioX: float = 0.5,
        ratioY: float = 0.5,
        wheelTimes: int = 1,
        interval: float = 0.05,
        waitTime: float = OPERATION_WAIT_TIME,
    ) -> None:
        """
        Make control have focus first, move cursor to the specified position and mouse wheel down.
        x: int, if < 0, move x cursor to self.BoundingRectangle.right + x, if not None, ignore ratioX.
        y: int, if < 0, move y cursor to self.BoundingRectangle.bottom + y, if not None, ignore ratioY.
        ratioX: float.
        ratioY: float.
        wheelTimes: int.
        interval: float.
        waitTime: float.
        """
        cursorX, cursorY = GetCursorPos()
        self.SetFocus()
        self.MoveCursorToInnerPos(x, y, ratioX, ratioY, simulateMove=False)
        WheelDown(wheelTimes, interval, waitTime)
        SetCursorPos(cursorX, cursorY)

    def WheelUp(
        self,
        x: int | None = None,
        y: int | None = None,
        ratioX: float = 0.5,
        ratioY: float = 0.5,
        wheelTimes: int = 1,
        interval: float = 0.05,
        waitTime: float = OPERATION_WAIT_TIME,
    ) -> None:
        """
        Make control have focus first, move cursor to the specified position and mouse wheel up.
        x: int, if < 0, move x cursor to self.BoundingRectangle.right + x, if not None, ignore ratioX.
        y: int, if < 0, move y cursor to self.BoundingRectangle.bottom + y, if not None, ignore ratioY.
        ratioX: float.
        ratioY: float.
        wheelTimes: int.
        interval: float.
        waitTime: float.
        """
        cursorX, cursorY = GetCursorPos()
        self.SetFocus()
        self.MoveCursorToInnerPos(x, y, ratioX, ratioY, simulateMove=False)
        WheelUp(wheelTimes, interval, waitTime)
        SetCursorPos(cursorX, cursorY)

    def ShowWindow(self, cmdShow: int, waitTime: float = OPERATION_WAIT_TIME) -> bool | None:
        """
        Get a native handle from self or ancestors until valid and call native `ShowWindow` with cmdShow.
        cmdShow: int, a value in in class `SW`.
        waitTime: float.
        Return bool, True if succeed otherwise False and None if the handle could not be gotten.
        """
        handle = self.NativeWindowHandle
        if not handle:
            control = self
            while not handle and control:
                control = control.GetParentControl()
                if control:
                    handle = control.NativeWindowHandle
                else:
                    handle = 0
                    break
        if handle:
            ret = ShowWindow(handle, cmdShow)
            time.sleep(waitTime)
            return ret
        return None

    def Show(self, waitTime: float = OPERATION_WAIT_TIME) -> bool | None:
        """
        Call native `ShowWindow(SW.Show)`.
        Return bool, True if succeed otherwise False and None if the handle could not be gotten.
        """
        return self.ShowWindow(SW.Show, waitTime)

    def Hide(self, waitTime: float = OPERATION_WAIT_TIME) -> bool | None:
        """
        Call native `ShowWindow(SW.Hide)`.
        waitTime: float
        Return bool, True if succeed otherwise False and None if the handle could not be gotten.
        """
        return self.ShowWindow(SW.Hide, waitTime)

    def MoveWindow(self, x: int, y: int, width: int, height: int, repaint: bool = True) -> bool:
        """
        Call native MoveWindow if control has a valid native handle.
        x: int.
        y: int.
        width: int.
        height: int.
        repaint: bool.
        Return bool, True if succeed otherwise False.
        """
        handle = self.NativeWindowHandle
        if handle:
            return MoveWindow(handle, x, y, width, height, int(repaint))
        return False

    def GetWindowText(self) -> str | None:
        """
        Call native GetWindowText if control has a valid native handle.
        """
        handle = self.NativeWindowHandle
        if handle:
            return GetWindowText(handle)
        return None

    def SetWindowText(self, text: str) -> bool:
        """
        Call native SetWindowText if control has a valid native handle.
        """
        handle = self.NativeWindowHandle
        if handle:
            return SetWindowText(handle, text)
        return False

    def SendKey(self, key: int, waitTime: float = OPERATION_WAIT_TIME) -> None:
        """
        Make control have focus first and type a key.
        `self.SetFocus` may not work for some controls, you may need to click it to make it have focus.
        key: int, a key code value in class Keys.
        waitTime: float.
        """
        self.SetFocus()
        SendKey(key, waitTime)

    def SendKeys(
        self,
        text: str,
        interval: float = 0.01,
        waitTime: float = OPERATION_WAIT_TIME,
        charMode: bool = True,
    ) -> None:
        """
        Make control have focus first and type keys.
        `self.SetFocus` may not work for some controls, you may need to click it to make it have focus.
        text: str, keys to type, see the docstring of `SendKeys`.
        interval: float, seconds between keys.
        waitTime: float.
        charMode: bool, if False, the text typied is depend on the input method if a input method is on.
        """
        self.SetFocus()
        SendKeys(text, interval, waitTime, charMode)

    def IsTopLevel(self) -> bool:
        """Determine whether current control is top level."""
        handle = self.NativeWindowHandle
        if handle:
            return GetAncestor(handle, GAFlag.Root) == handle
        return False

    def GetTopLevelControl(self) -> "Control" | None:
        """
        Get the top level control which current control lays.
        If current control is top level, return self.
        If current control is root control, return None.
        Return `PaneControl` or `WindowControl` or None.
        """
        handle = self.NativeWindowHandle
        if handle:
            topHandle = GetAncestor(handle, GAFlag.Root)
            if topHandle:
                if topHandle == handle:
                    return self
                else:
                    return ControlFromHandle(topHandle)
            else:
                # self is root control
                pass
        else:
            control = self
            while True:
                control = control.GetParentControl()
                if not control:
                    break
                handle = control.NativeWindowHandle
                if handle:
                    topHandle = GetAncestor(handle, GAFlag.Root)
                    return ControlFromHandle(topHandle)
        return None

    def Control(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "Control":
        return Control(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def ButtonControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "ButtonControl":
        return ButtonControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def CalendarControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "CalendarControl":
        return CalendarControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def CheckBoxControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "CheckBoxControl":
        return CheckBoxControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def ComboBoxControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "ComboBoxControl":
        return ComboBoxControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def CustomControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "CustomControl":
        return CustomControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def DataGridControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "DataGridControl":
        return DataGridControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def DataItemControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "DataItemControl":
        return DataItemControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def DocumentControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "DocumentControl":
        return DocumentControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def EditControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "EditControl":
        return EditControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GroupControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "GroupControl":
        return GroupControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def HeaderControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "HeaderControl":
        return HeaderControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def HeaderItemControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "HeaderItemControl":
        return HeaderItemControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def HyperlinkControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "HyperlinkControl":
        return HyperlinkControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def ImageControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "ImageControl":
        return ImageControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def ListControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "ListControl":
        return ListControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def ListItemControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "ListItemControl":
        return ListItemControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def MenuControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "MenuControl":
        return MenuControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def MenuBarControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "MenuBarControl":
        return MenuBarControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def MenuItemControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "MenuItemControl":
        return MenuItemControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def PaneControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "PaneControl":
        return PaneControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def ProgressBarControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "ProgressBarControl":
        return ProgressBarControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def RadioButtonControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "RadioButtonControl":
        return RadioButtonControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def ScrollBarControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "ScrollBarControl":
        return ScrollBarControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def SemanticZoomControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "SemanticZoomControl":
        return SemanticZoomControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def SeparatorControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "SeparatorControl":
        return SeparatorControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def SliderControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "SliderControl":
        return SliderControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def SpinnerControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "SpinnerControl":
        return SpinnerControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def SplitButtonControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "SplitButtonControl":
        return SplitButtonControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def StatusBarControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "StatusBarControl":
        return StatusBarControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def TabControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "TabControl":
        return TabControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def TabItemControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "TabItemControl":
        return TabItemControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def TableControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "TableControl":
        return TableControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def TextControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "TextControl":
        return TextControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def ThumbControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "ThumbControl":
        return ThumbControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def TitleBarControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "TitleBarControl":
        return TitleBarControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def ToolBarControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "ToolBarControl":
        return ToolBarControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def ToolTipControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "ToolTipControl":
        return ToolTipControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def TreeControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "TreeControl":
        return TreeControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def TreeItemControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "TreeItemControl":
        return TreeItemControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def WindowControl(
        self,
        searchDepth=0xFFFFFFFF,
        searchInterval=SEARCH_INTERVAL,
        foundIndex=1,
        element=0,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ) -> "WindowControl":
        return WindowControl(
            searchFromControl=self,
            searchDepth=searchDepth,
            searchInterval=searchInterval,
            foundIndex=foundIndex,
            element=element,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )


class AppBarControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.AppBarControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )


class ButtonControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.ButtonControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetExpandCollapsePattern(self) -> ExpandCollapsePattern:
        """
        Return `ExpandCollapsePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ExpandCollapsePattern)

    def GetInvokePattern(self) -> InvokePattern:
        """
        Return `InvokePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.InvokePattern)

    def GetTogglePattern(self) -> TogglePattern:
        """
        Return `TogglePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TogglePattern)


class CalendarControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.CalendarControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetGridPattern(self) -> GridPattern:
        """
        Return `GridPattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.GridPattern)

    def GetTablePattern(self) -> TablePattern:
        """
        Return `TablePattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.TablePattern)

    def GetScrollPattern(self) -> ScrollPattern:
        """
        Return `ScrollPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ScrollPattern)

    def GetSelectionPattern(self) -> SelectionPattern:
        """
        Return `SelectionPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.SelectionPattern)


class CheckBoxControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.CheckBoxControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetTogglePattern(self) -> TogglePattern:
        """
        Return `TogglePattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.TogglePattern)

    def SetChecked(self, checked: bool) -> bool:
        """Return True if set successfully"""
        tp = self.GetTogglePattern()
        if tp:
            return tp.SetToggleState(ToggleState.On if checked else ToggleState.Off)
        return False


class ComboBoxControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.ComboBoxControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetExpandCollapsePattern(self) -> ExpandCollapsePattern:
        """
        Return `ExpandCollapsePattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.ExpandCollapsePattern)

    def GetSelectionPattern(self) -> SelectionPattern:
        """
        Return `SelectionPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.SelectionPattern)

    def GetValuePattern(self) -> ValuePattern:
        """
        Return `ValuePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ValuePattern)

    def Select(
        self,
        itemName: str = "",
        condition: Callable[[str], bool] | None = None,
        simulateMove: bool = True,
        waitTime: float = OPERATION_WAIT_TIME,
    ) -> bool:
        """
        Show combobox's popup menu and select a item by name.
        itemName: str.
        condition: Callable[[str], bool], function(comboBoxItemName: str) -> bool, if condition is valid, ignore itemName.
        waitTime: float.
        Some comboboxs doesn't support SelectionPattern, here is a workaround.
        This method tries to add selection support.
        It may not work for some comboboxes, such as comboboxes in older Qt version.
        If it doesn't work, you should write your own version Select, or it doesn't support selection at all.
        """
        expandCollapsePattern = self.GetExpandCollapsePattern()
        if expandCollapsePattern:
            expandCollapsePattern.Expand()
        else:
            # Windows Form's ComboBoxControl doesn't support ExpandCollapsePattern
            self.Click(x=-10, ratioY=0.5, simulateMove=simulateMove)
        find = False
        if condition:
            listItemControl = self.ListItemControl(Compare=lambda c, d: condition(c.Name))
        else:
            listItemControl = self.ListItemControl(Name=itemName)
        if listItemControl.Exists(1):
            scrollItemPattern = listItemControl.GetScrollItemPattern()
            if scrollItemPattern:
                scrollItemPattern.ScrollIntoView(waitTime=0.1)
            listItemControl.Click(simulateMove=simulateMove, waitTime=waitTime)
            find = True
        else:
            # some ComboBox's popup window is a child of root control
            listControl = ListControl(searchDepth=1)
            if listControl.Exists(1):
                if condition:
                    listItemControl = listControl.ListItemControl(
                        Compare=lambda c, d: condition(c.Name)
                    )
                else:
                    listItemControl = listControl.ListItemControl(Name=itemName)
                if listItemControl.Exists(0, 0):
                    scrollItemPattern = listItemControl.GetScrollItemPattern()
                    if scrollItemPattern:
                        scrollItemPattern.ScrollIntoView(waitTime=0.1)
                    listItemControl.Click(simulateMove=simulateMove, waitTime=waitTime)
                    find = True
        if not find:
            if expandCollapsePattern:
                expandCollapsePattern.Collapse(waitTime)
            else:
                self.Click(x=-10, ratioY=0.5, simulateMove=simulateMove, waitTime=waitTime)
        return find


class CustomControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.CustomControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )


class DataGridControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.DataGridControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetGridPattern(self) -> GridPattern:
        """
        Return `GridPattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.GridPattern)

    def GetScrollPattern(self) -> ScrollPattern:
        """
        Return `ScrollPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ScrollPattern)

    def GetSelectionPattern(self) -> SelectionPattern:
        """
        Return `SelectionPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.SelectionPattern)

    def GetTablePattern(self) -> TablePattern:
        """
        Return `TablePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TablePattern)


class DataItemControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.DataItemControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetSelectionItemPattern(self) -> SelectionItemPattern:
        """
        Return `SelectionItemPattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.SelectionItemPattern)

    def GetExpandCollapsePattern(self) -> ExpandCollapsePattern:
        """
        Return `ExpandCollapsePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ExpandCollapsePattern)

    def GetGridItemPattern(self) -> GridItemPattern:
        """
        Return `GridItemPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.GridItemPattern)

    def GetScrollItemPattern(self) -> ScrollItemPattern:
        """
        Return `ScrollItemPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ScrollItemPattern)

    def GetTableItemPattern(self) -> TableItemPattern:
        """
        Return `TableItemPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TableItemPattern)

    def GetTogglePattern(self) -> TogglePattern:
        """
        Return `TogglePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TogglePattern)

    def GetValuePattern(self) -> ValuePattern:
        """
        Return `ValuePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ValuePattern)


class DocumentControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.DocumentControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetTextPattern(self) -> TextPattern:
        """
        Return `TextPattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.TextPattern)

    def GetScrollPattern(self) -> ScrollPattern:
        """
        Return `ScrollPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ScrollPattern)

    def GetValuePattern(self) -> ValuePattern:
        """
        Return `ValuePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ValuePattern)


class EditControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.EditControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetRangeValuePattern(self) -> RangeValuePattern:
        """
        Return `RangeValuePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.RangeValuePattern)

    def GetTextPattern(self) -> TextPattern:
        """
        Return `TextPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TextPattern)

    def GetValuePattern(self) -> ValuePattern:
        """
        Return `ValuePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ValuePattern)


class GroupControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.GroupControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetExpandCollapsePattern(self) -> ExpandCollapsePattern:
        """
        Return `ExpandCollapsePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ExpandCollapsePattern)


class HeaderControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.HeaderControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetTransformPattern(self) -> TransformPattern:
        """
        Return `TransformPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TransformPattern)


class HeaderItemControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.HeaderItemControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetInvokePattern(self) -> InvokePattern:
        """
        Return `InvokePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.InvokePattern)

    def GetTransformPattern(self) -> TransformPattern:
        """
        Return `TransformPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TransformPattern)


class HyperlinkControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.HyperlinkControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetInvokePattern(self) -> InvokePattern:
        """
        Return `InvokePattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.InvokePattern)

    def GetValuePattern(self) -> ValuePattern:
        """
        Return `ValuePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ValuePattern)


class ImageControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.ImageControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetGridItemPattern(self) -> GridItemPattern:
        """
        Return `GridItemPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.GridItemPattern)

    def GetTableItemPattern(self) -> TableItemPattern:
        """
        Return `TableItemPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TableItemPattern)


class ListControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.ListControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetGridPattern(self) -> GridPattern:
        """
        Return `GridPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.GridPattern)

    def GetMultipleViewPattern(self) -> MultipleViewPattern:
        """
        Return `MultipleViewPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.MultipleViewPattern)

    def GetScrollPattern(self) -> ScrollPattern:
        """
        Return `ScrollPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ScrollPattern)

    def GetSelectionPattern(self) -> SelectionPattern:
        """
        Return `SelectionPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.SelectionPattern)


class ListItemControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.ListItemControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetSelectionItemPattern(self) -> SelectionItemPattern:
        """
        Return `SelectionItemPattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.SelectionItemPattern)

    def GetExpandCollapsePattern(self) -> ExpandCollapsePattern:
        """
        Return `ExpandCollapsePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ExpandCollapsePattern)

    def GetGridItemPattern(self) -> GridItemPattern:
        """
        Return `GridItemPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.GridItemPattern)

    def GetInvokePattern(self) -> InvokePattern:
        """
        Return `InvokePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.InvokePattern)

    def GetScrollItemPattern(self) -> ScrollItemPattern:
        """
        Return `ScrollItemPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ScrollItemPattern)

    def GetTogglePattern(self) -> TogglePattern:
        """
        Return `TogglePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TogglePattern)

    def GetValuePattern(self) -> ValuePattern:
        """
        Return `ValuePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ValuePattern)


class MenuControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.MenuControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )


class MenuBarControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.MenuBarControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetDockPattern(self) -> DockPattern:
        """
        Return `DockPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.DockPattern)

    def GetExpandCollapsePattern(self) -> ExpandCollapsePattern:
        """
        Return `ExpandCollapsePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ExpandCollapsePattern)

    def GetTransformPattern(self) -> TransformPattern:
        """
        Return `TransformPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TransformPattern)


class MenuItemControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.MenuItemControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetExpandCollapsePattern(self) -> ExpandCollapsePattern:
        """
        Return `ExpandCollapsePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ExpandCollapsePattern)

    def GetInvokePattern(self) -> InvokePattern:
        """
        Return `InvokePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.InvokePattern)

    def GetSelectionItemPattern(self) -> SelectionItemPattern:
        """
        Return `SelectionItemPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.SelectionItemPattern)

    def GetTogglePattern(self) -> TogglePattern:
        """
        Return `TogglePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TogglePattern)


class TopLevel:
    """Class TopLevel"""

    def SetTopmost(self, isTopmost: bool = True, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Set top level window topmost.
        isTopmost: bool.
        waitTime: float.
        """
        if self.IsTopLevel():
            ret = SetWindowTopmost(self.NativeWindowHandle, isTopmost)
            time.sleep(waitTime)
            return ret
        return False

    def IsTopmost(self) -> bool:
        if self.IsTopLevel():
            WS_EX_TOPMOST = 0x00000008
            return bool(GetWindowLong(self.NativeWindowHandle, GWL.ExStyle) & WS_EX_TOPMOST)
        return False

    def SwitchToThisWindow(self, waitTime: float = OPERATION_WAIT_TIME) -> None:
        if self.IsTopLevel():
            SwitchToThisWindow(self.NativeWindowHandle)
            time.sleep(waitTime)

    def Maximize(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Set top level window maximize.
        """
        if self.IsTopLevel():
            return self.ShowWindow(SW.ShowMaximized, waitTime)
        return False

    def IsMaximize(self) -> bool:
        if self.IsTopLevel():
            return bool(IsZoomed(self.NativeWindowHandle))
        return False

    def Minimize(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        if self.IsTopLevel():
            return self.ShowWindow(SW.Minimize, waitTime)
        return False

    def IsMinimize(self) -> bool:
        if self.IsTopLevel():
            return bool(IsIconic(self.NativeWindowHandle))
        return False

    def Restore(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Restore window to normal state.
        Similar to SwitchToThisWindow.
        """
        if self.IsTopLevel():
            return self.ShowWindow(SW.Restore, waitTime)
        return False

    def MoveToCenter(self) -> bool:
        """
        Move window to screen center.
        """
        if self.IsTopLevel():
            rect = self.BoundingRectangle
            screenWidth, screenHeight = GetScreenSize()
            x, y = (
                (screenWidth - rect.width()) // 2,
                (screenHeight - rect.height()) // 2,
            )
            if x < 0:
                x = 0
            if y < 0:
                y = 0
            return SetWindowPos(self.NativeWindowHandle, SWP.HWND_Top, x, y, 0, 0, SWP.SWP_NoSize)
        return False

    def SetActive(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """Set top level window active."""
        if self.IsTopLevel():
            handle = self.NativeWindowHandle
            if IsIconic(handle):
                ret = ShowWindow(handle, SW.Restore)
            elif not IsWindowVisible(handle):
                ret = ShowWindow(handle, SW.Show)
            ret = SetForegroundWindow(
                handle
            )  # may fail if foreground windows's process is not python
            time.sleep(waitTime)
            return ret
        return False


class PaneControl(Control, TopLevel):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.PaneControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetDockPattern(self) -> DockPattern:
        """
        Return `DockPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.DockPattern)

    def GetScrollPattern(self) -> ScrollPattern:
        """
        Return `ScrollPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ScrollPattern)

    def GetTransformPattern(self) -> TransformPattern:
        """
        Return `TransformPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TransformPattern)


class ProgressBarControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.ProgressBarControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetRangeValuePattern(self) -> RangeValuePattern:
        """
        Return `RangeValuePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.RangeValuePattern)

    def GetValuePattern(self) -> ValuePattern:
        """
        Return `ValuePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ValuePattern)


class RadioButtonControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.RadioButtonControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetSelectionItemPattern(self) -> SelectionItemPattern:
        """
        Return `SelectionItemPattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.SelectionItemPattern)


class ScrollBarControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.ScrollBarControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetRangeValuePattern(self) -> RangeValuePattern:
        """
        Return `RangeValuePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.RangeValuePattern)


class SemanticZoomControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.SemanticZoomControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )


class SeparatorControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.SeparatorControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )


class SliderControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.SliderControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetRangeValuePattern(self) -> RangeValuePattern:
        """
        Return `RangeValuePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.RangeValuePattern)

    def GetSelectionPattern(self) -> SelectionPattern:
        """
        Return `SelectionPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.SelectionPattern)

    def GetValuePattern(self) -> ValuePattern:
        """
        Return `ValuePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ValuePattern)


class SpinnerControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.SpinnerControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetRangeValuePattern(self) -> RangeValuePattern:
        """
        Return `RangeValuePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.RangeValuePattern)

    def GetSelectionPattern(self) -> SelectionPattern:
        """
        Return `SelectionPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.SelectionPattern)

    def GetValuePattern(self) -> ValuePattern:
        """
        Return `ValuePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ValuePattern)


class SplitButtonControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.SplitButtonControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetExpandCollapsePattern(self) -> ExpandCollapsePattern:
        """
        Return `ExpandCollapsePattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.ExpandCollapsePattern)

    def GetInvokePattern(self) -> InvokePattern:
        """
        Return `InvokePattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.InvokePattern)


class StatusBarControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.StatusBarControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetGridPattern(self) -> GridPattern:
        """
        Return `GridPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.GridPattern)


class TabControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.TabControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetSelectionPattern(self) -> SelectionPattern:
        """
        Return `SelectionPattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.SelectionPattern)

    def GetScrollPattern(self) -> ScrollPattern:
        """
        Return `ScrollPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ScrollPattern)


class TabItemControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.TabItemControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetSelectionItemPattern(self) -> SelectionItemPattern:
        """
        Return `SelectionItemPattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.SelectionItemPattern)


class TableControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.TableControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetGridPattern(self) -> GridPattern:
        """
        Return `GridPattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.GridPattern)

    def GetGridItemPattern(self) -> GridItemPattern:
        """
        Return `GridItemPattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.GridItemPattern)

    def GetTablePattern(self) -> TablePattern:
        """
        Return `TablePattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.TablePattern)

    def GetTableItemPattern(self) -> TableItemPattern:
        """
        Return `TableItemPattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.TableItemPattern)

    def GetTableItemsValue(self, row: int = -1, column: int = -1):
        """
        Get the value of a table
        row: int. Position of the row in the table
        column: int. Position of the column in the table
        Return a list with values in the table.
        If a row and column is specified, return a cell value.
        If only a row is specified, return a list with row values
        """
        table = []
        for item in self.GetChildren():
            table.append([cell.GetLegacyIAccessiblePattern().Value for cell in item.GetChildren()])
        if row > 0 and column > 0:
            return table[row][column]
        if row > 0:
            return table[row]
        return table


class TextControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.TextControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetGridItemPattern(self) -> GridItemPattern:
        """
        Return `GridItemPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.GridItemPattern)

    def GetTableItemPattern(self) -> TableItemPattern:
        """
        Return `TableItemPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TableItemPattern)

    def GetTextPattern(self) -> TextPattern:
        """
        Return `TextPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TextPattern)


class ThumbControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.ThumbControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetTransformPattern(self) -> TransformPattern:
        """
        Return `TransformPattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.TransformPattern)


class TitleBarControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.TitleBarControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )


class ToolBarControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.ToolBarControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetDockPattern(self) -> DockPattern:
        """
        Return `DockPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.DockPattern)

    def GetExpandCollapsePattern(self) -> ExpandCollapsePattern:
        """
        Return `ExpandCollapsePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ExpandCollapsePattern)

    def GetTransformPattern(self) -> TransformPattern:
        """
        Return `TransformPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TransformPattern)


class ToolTipControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.ToolTipControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetTextPattern(self) -> TextPattern:
        """
        Return `TextPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TextPattern)

    def GetWindowPattern(self) -> WindowPattern:
        """
        Return `WindowPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.WindowPattern)


class TreeControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.TreeControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetScrollPattern(self) -> ScrollPattern:
        """
        Return `ScrollPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ScrollPattern)

    def GetSelectionPattern(self) -> SelectionPattern:
        """
        Return `SelectionPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.SelectionPattern)


class TreeItemControl(Control):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.TreeItemControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )

    def GetExpandCollapsePattern(self) -> ExpandCollapsePattern:
        """
        Return `ExpandCollapsePattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.ExpandCollapsePattern)

    def GetInvokePattern(self) -> InvokePattern:
        """
        Return `InvokePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.InvokePattern)

    def GetScrollItemPattern(self) -> ScrollItemPattern:
        """
        Return `ScrollItemPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.ScrollItemPattern)

    def GetSelectionItemPattern(self) -> SelectionItemPattern:
        """
        Return `SelectionItemPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.SelectionItemPattern)

    def GetTogglePattern(self) -> TogglePattern:
        """
        Return `TogglePattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.TogglePattern)


class WindowControl(Control, TopLevel):
    def __init__(
        self,
        searchFromControl: Control | None = None,
        searchDepth: int = 0xFFFFFFFF,
        searchInterval: float = SEARCH_INTERVAL,
        foundIndex: int = 1,
        element=None,
        Name: str | None = None,
        SubName: str | None = None,
        RegexName: str | None = None,
        ClassName: str | None = None,
        AutomationId: str | None = None,
        Depth: int | None = None,
        Compare: Callable[[TreeNode], bool] | None = None,
        **searchProperties,
    ):
        Control.__init__(
            self,
            searchFromControl,
            searchDepth,
            searchInterval,
            foundIndex,
            element,
            ControlType=ControlType.WindowControl,
            Name=Name,
            SubName=SubName,
            RegexName=RegexName,
            ClassName=ClassName,
            AutomationId=AutomationId,
            Depth=Depth,
            Compare=Compare,
            **searchProperties,
        )
        self._DockPattern = None
        self._TransformPattern = None

    def GetTransformPattern(self) -> TransformPattern:
        """
        Return `TransformPattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.TransformPattern)

    def GetWindowPattern(self) -> WindowPattern:
        """
        Return `WindowPattern` if it supports the pattern else None(Must support according to MSDN).
        """
        return self.GetPattern(PatternId.WindowPattern)

    def GetDockPattern(self) -> DockPattern:
        """
        Return `DockPattern` if it supports the pattern else None(Conditional support according to MSDN).
        """
        return self.GetPattern(PatternId.DockPattern)

    def MetroClose(self, waitTime: float = OPERATION_WAIT_TIME) -> None:
        """
        Only work on Windows 8/8.1, if current window is Metro UI.
        waitTime: float.
        """
        if self.ClassName == METRO_WINDOW_CLASS_NAME:
            screenWidth, screenHeight = GetScreenSize()
            MoveTo(screenWidth // 2, 0, waitTime=0)
            DragDrop(screenWidth // 2, 0, screenWidth // 2, screenHeight, waitTime=waitTime)
        else:
            pass


ControlConstructors = {
    ControlType.AppBarControl: AppBarControl,
    ControlType.ButtonControl: ButtonControl,
    ControlType.CalendarControl: CalendarControl,
    ControlType.CheckBoxControl: CheckBoxControl,
    ControlType.ComboBoxControl: ComboBoxControl,
    ControlType.CustomControl: CustomControl,
    ControlType.DataGridControl: DataGridControl,
    ControlType.DataItemControl: DataItemControl,
    ControlType.DocumentControl: DocumentControl,
    ControlType.EditControl: EditControl,
    ControlType.GroupControl: GroupControl,
    ControlType.HeaderControl: HeaderControl,
    ControlType.HeaderItemControl: HeaderItemControl,
    ControlType.HyperlinkControl: HyperlinkControl,
    ControlType.ImageControl: ImageControl,
    ControlType.ListControl: ListControl,
    ControlType.ListItemControl: ListItemControl,
    ControlType.MenuBarControl: MenuBarControl,
    ControlType.MenuControl: MenuControl,
    ControlType.MenuItemControl: MenuItemControl,
    ControlType.PaneControl: PaneControl,
    ControlType.ProgressBarControl: ProgressBarControl,
    ControlType.RadioButtonControl: RadioButtonControl,
    ControlType.ScrollBarControl: ScrollBarControl,
    ControlType.SemanticZoomControl: SemanticZoomControl,
    ControlType.SeparatorControl: SeparatorControl,
    ControlType.SliderControl: SliderControl,
    ControlType.SpinnerControl: SpinnerControl,
    ControlType.SplitButtonControl: SplitButtonControl,
    ControlType.StatusBarControl: StatusBarControl,
    ControlType.TabControl: TabControl,
    ControlType.TabItemControl: TabItemControl,
    ControlType.TableControl: TableControl,
    ControlType.TextControl: TextControl,
    ControlType.ThumbControl: ThumbControl,
    ControlType.TitleBarControl: TitleBarControl,
    ControlType.ToolBarControl: ToolBarControl,
    ControlType.ToolTipControl: ToolTipControl,
    ControlType.TreeControl: TreeControl,
    ControlType.TreeItemControl: TreeItemControl,
    ControlType.WindowControl: WindowControl,
}


class UIAutomationInitializerInThread:
    def __init__(self, debug: bool = False):
        self.debug = debug
        InitializeUIAutomationInCurrentThread()
        self.inited = True
        if self.debug:
            th = threading.currentThread()
            print(
                "\ncall InitializeUIAutomationInCurrentThread in {}, inited {}".format(
                    th, self.inited
                )
            )

    def __del__(self):
        self.Uninitialize()

    def __enter__(self):
        return self

    def __exit__(self, exceptionType, exceptionValue, exceptionTraceback):
        self.Uninitialize()

    def Uninitialize(self):
        if self.inited:
            UninitializeUIAutomationInCurrentThread()
            self.inited = False
            if self.debug:
                th = threading.currentThread()
                print("\ncall UninitializeUIAutomationInCurrentThread in {}".format(th))


def InitializeUIAutomationInCurrentThread() -> None:
    """
    Initialize UIAutomation in a new thread.
    If you want to use functionalities related to Controls and Patterns in a new thread.
    You must call this function first in the new thread.
    But you can't use use a Control or a Pattern created in a different thread.
    So you can't create a Control or a Pattern in main thread and then pass it to a new thread and use it.
    """
    comtypes.CoInitializeEx()


def UninitializeUIAutomationInCurrentThread() -> None:
    """
    Uninitialize UIAutomation in a new thread after calling InitializeUIAutomationInCurrentThread.
    You must call this function when the new thread exits if you have called InitializeUIAutomationInCurrentThread in the same thread.
    """
    comtypes.CoUninitialize()


def SetGlobalSearchTimeout(seconds: float) -> None:
    """
    seconds: float.
    To make this available, you need explicitly import uiautomation:
        from uiautomation import uiautomation as auto
        auto.SetGlobalSearchTimeout(10)
    """
    global TIME_OUT_SECOND
    TIME_OUT_SECOND = seconds


def WaitForExist(control: Control, timeout: float) -> bool:
    """
    Check if control exists in timeout seconds.
    control: `Control` or its subclass.
    timeout: float.
    Return bool.
    """
    return control.Exists(timeout, 1)


def WaitForDisappear(control: Control, timeout: float) -> bool:
    """
    Check if control disappears in timeout seconds.
    control: `Control` or its subclass.
    timeout: float.
    Return bool.
    """
    return control.Disappears(timeout, 1)


def WalkTree(
    top,
    getChildren: Callable[[TreeNode], List[TreeNode]] | None = None,
    getFirstChild: Callable[[TreeNode], TreeNode] | None = None,
    getNextSibling: Callable[[TreeNode], TreeNode] | None = None,
    yieldCondition: Callable[[TreeNode, int], bool] | None = None,
    includeTop: bool = False,
    maxDepth: int = 0xFFFFFFFF,
):
    """
    Walk a tree not using recursive algorithm.
    top: a tree node.
    getChildren: Callable[[TreeNode], List[TreeNode]], function(treeNode: TreeNode) -> List[TreeNode].
    getNextSibling: Callable[[TreeNode], TreeNode], function(treeNode: TreeNode) -> TreeNode.
    getNextSibling: Callable[[TreeNode], TreeNode], function(treeNode: TreeNode) -> TreeNode.
    yieldCondition: Callable[[TreeNode, int], bool], function(treeNode: TreeNode, depth: int) -> bool.
    includeTop: bool, if True yield top first.
    maxDepth: int, enum depth.

    If getChildren is valid, ignore getFirstChild and getNextSibling,
        yield 3 items tuple: (treeNode, depth, remain children count in current depth).
    If getChildren is not valid, using getFirstChild and getNextSibling,
        yield 2 items tuple: (treeNode, depth).
    If yieldCondition is not None, only yield tree nodes that yieldCondition(treeNode: TreeNode, depth: int)->bool returns True.

    For example:
    def GetDirChildren(dir_):
        if os.path.isdir(dir_):
            return [os.path.join(dir_, it) for it in os.listdir(dir_)]
    for it, depth, leftCount in WalkTree('D:\\', getChildren= GetDirChildren):
        print(it, depth, leftCount)
    """
    if maxDepth <= 0:
        return
    depth = 0
    if getChildren:
        if includeTop:
            if not yieldCondition or yieldCondition(top, 0):
                yield top, 0, 0
        children = getChildren(top)
        childList = [children]
        while depth >= 0:  # or while childList:
            lastItems = childList[-1]
            if lastItems:
                if not yieldCondition or yieldCondition(lastItems[0], depth + 1):
                    yield lastItems[0], depth + 1, len(lastItems) - 1
                if depth + 1 < maxDepth:
                    children = getChildren(lastItems[0])
                    if children:
                        depth += 1
                        childList.append(children)
                del lastItems[0]
            else:
                del childList[depth]
                depth -= 1
    elif getFirstChild and getNextSibling:
        if includeTop:
            if not yieldCondition or yieldCondition(top, 0):
                yield top, 0
        child = getFirstChild(top)
        childList = [child]
        while depth >= 0:  # or while childList:
            lastItem = childList[-1]
            if lastItem:
                if not yieldCondition or yieldCondition(lastItem, depth + 1):
                    yield lastItem, depth + 1
                child = getNextSibling(lastItem)
                childList[depth] = child
                if depth + 1 < maxDepth:
                    child = getFirstChild(lastItem)
                    if child:
                        depth += 1
                        childList.append(child)
            else:
                del childList[depth]
                depth -= 1


def GetRootControl() -> PaneControl:
    """
    Get root control, the Desktop window.
    Return `PaneControl`.
    """
    control = Control.CreateControlFromElement(
        _AutomationClient.instance().IUIAutomation.GetRootElement()
    )
    if isinstance(control, PaneControl):
        return control

    if control is None:
        raise AssertionError("Expected valid root element")
    raise AssertionError(
        "Expected root element to be a PaneControl. Found: %s (%s)" % (type(control), control)
    )


def GetFocusedControl() -> Control | None:
    """Return `Control` subclass."""
    return Control.CreateControlFromElement(
        _AutomationClient.instance().IUIAutomation.GetFocusedElement()
    )


def GetForegroundControl() -> Control:
    """Return `Control` subclass."""
    return ControlFromHandle(GetForegroundWindow())
    # another implement
    # focusedControl = GetFocusedControl()
    # parentControl = focusedControl
    # controlList = []
    # while parentControl:
    # controlList.insert(0, parentControl)
    # parentControl = parentControl.GetParentControl()
    # if len(controlList) == 1:
    # parentControl = controlList[0]
    # else:
    # parentControl = controlList[1]
    # return parentControl


def GetConsoleWindow() -> WindowControl | None:
    """Return `WindowControl` or None, a console window that runs python."""
    consoleWindow = ControlFromHandle(ctypes.windll.kernel32.GetConsoleWindow())
    if consoleWindow and consoleWindow.ClassName == "PseudoConsoleWindow":
        # Windows Terminal
        consoleWindow = consoleWindow.GetParentControl()
    return consoleWindow


def ControlFromPoint(x: int, y: int) -> Control | None:
    """
    Call IUIAutomation ElementFromPoint x,y. May return None if mouse is over cmd's title bar icon.
    Return `Control` subclass or None.
    """
    element = _AutomationClient.instance().IUIAutomation.ElementFromPoint(
        ctypes.wintypes.POINT(x, y)
    )
    return Control.CreateControlFromElement(element)


def ControlFromPoint2(x: int, y: int) -> Control | None:
    """
    Get a native handle from point x,y and call IUIAutomation.ElementFromHandle.
    Return `Control` subclass.
    """
    return Control.CreateControlFromElement(
        _AutomationClient.instance().IUIAutomation.ElementFromHandle(WindowFromPoint(x, y))
    )


def ControlFromCursor() -> Control | None:
    """
    Call ControlFromPoint with current cursor point.
    Return `Control` subclass.
    """
    x, y = GetCursorPos()
    return ControlFromPoint(x, y)


def ControlFromCursor2() -> Control | None:
    """
    Call ControlFromPoint2 with current cursor point.
    Return `Control` subclass.
    """
    x, y = GetCursorPos()
    return ControlFromPoint2(x, y)


def ControlFromHandle(handle: int) -> Control | None:
    """
    Call IUIAutomation.ElementFromHandle with a native handle.
    handle: int, a native window handle.
    Return `Control` subclass or None.
    """
    if handle:
        return Control.CreateControlFromElement(
            _AutomationClient.instance().IUIAutomation.ElementFromHandle(handle)
        )
    return None


def ControlsAreSame(control1: Control, control2: Control) -> bool:
    """
    control1: `Control` or its subclass.
    control2: `Control` or its subclass.
    Return bool, True if control1 and control2 represent the same control otherwise False.
    """
    return bool(
        _AutomationClient.instance().IUIAutomation.CompareElements(
            control1.Element, control2.Element
        )
    )


def WalkControl(
    control: Control, includeTop: bool = False, maxDepth: int = 0xFFFFFFFF
) -> Generator[Tuple[Control, int], None, None]:
    """
    control: `Control` or its subclass.
    includeTop: bool, if True, yield (control, 0) first.
    maxDepth: int, enum depth.
    Yield 2 items tuple (control: Control, depth: int).
    """
    if includeTop:
        yield control, 0
    if maxDepth <= 0:
        return
    depth = 0
    child = control.GetFirstChildControl()
    controlList = [child]
    while depth >= 0:
        lastControl = controlList[-1]
        if lastControl:
            yield lastControl, depth + 1
            child = lastControl.GetNextSiblingControl()
            controlList[depth] = child
            if depth + 1 < maxDepth:
                child = lastControl.GetFirstChildControl()
                if child:
                    depth += 1
                    controlList.append(child)
        else:
            del controlList[depth]
            depth -= 1


def LogControl(
    control: Control, depth: int = 0, showAllName: bool = True, showPid: bool = False
) -> None:
    """
    Print and log control's properties.
    control: `Control` or its subclass.
    depth: int, current depth.
    showAllName: bool, if False, print the first 30 characters of control.Name.
    """
    indent = " " * depth * 4
    if showPid:
        pass
    supportedPatterns = list(
        filter(
            lambda t: t[0],
            ((control.GetPattern(id_), name) for id_, name in PatternIdNames.items()),
        )
    )
    for pt, name in supportedPatterns:
        if isinstance(pt, ValuePattern):
            pass
        elif isinstance(pt, RangeValuePattern):
            pass
        elif isinstance(pt, TogglePattern):
            pass
        elif isinstance(pt, SelectionItemPattern):
            pass
        elif isinstance(pt, ExpandCollapsePattern):
            pass
        elif isinstance(pt, ScrollPattern):
            pass
        elif isinstance(pt, GridPattern):
            pass
        elif isinstance(pt, GridItemPattern):
            pass
        elif isinstance(pt, TextPattern):
            # issue 49: CEF Control as DocumentControl have no "TextPattern.Text" property, skip log this part.
            # https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextpattern-get_documentrange
            try:
                pass
            except comtypes.COMError:
                pass
    for pt, name in supportedPatterns:
        pass


def EnumAndLogControl(
    control: Control,
    maxDepth: int = 0xFFFFFFFF,
    showAllName: bool = True,
    showPid: bool = False,
    startDepth: int = 0,
) -> None:
    """
    Print and log control and its descendants' propertyies.
    control: `Control` or its subclass.
    maxDepth: int, enum depth.
    showAllName: bool, if False, print the first 30 characters of control.Name.
    startDepth: int, control's current depth.
    """
    for c, d in WalkControl(control, True, maxDepth):
        LogControl(c, d + startDepth, showAllName, showPid)


def EnumAndLogControlAncestors(
    control: Control, showAllName: bool = True, showPid: bool = False
) -> None:
    """
    Print and log control and its ancestors' propertyies.
    control: `Control` or its subclass.
    showAllName: bool, if False, print the first 30 characters of control.Name.
    """
    curr = control
    lists = []
    while curr:
        lists.insert(0, curr)
        curr = curr.GetParentControl()
    for i, curr in enumerate(lists):
        LogControl(curr, i, showAllName, showPid)


def FindControl(
    control: Control | None,
    compare: Callable[[Control, int], bool],
    maxDepth: int = 0xFFFFFFFF,
    findFromSelf: bool = False,
    foundIndex: int = 1,
) -> Control | None:
    """
    control: `Control` or its subclass.
    compare: Callable[[Control, int], bool], function(control: Control, depth: int) -> bool.
    maxDepth: int, enum depth.
    findFromSelf: bool, if False, do not compare self.
    foundIndex: int, starts with 1, >= 1.
    Return `Control` subclass or None if not find.
    """
    foundCount = 0
    if not control:
        control = GetRootControl()
    traverseCount = 0
    for child, depth in WalkControl(control, findFromSelf, maxDepth):
        traverseCount += 1
        if compare(child, depth):
            foundCount += 1
            if foundCount == foundIndex:
                child.traverseCount = traverseCount
                return child
    return None


def ShowDesktop(waitTime: float = 1) -> None:
    """Show Desktop by pressing win + d"""
    SendKeys("{Win}d", waitTime=waitTime)
    # another implement
    # paneTray = PaneControl(searchDepth = 1, ClassName = 'Shell_TrayWnd')
    # if paneTray.Exists():
    # WM_COMMAND = 0x111
    # MIN_ALL = 419
    # MIN_ALL_UNDO = 416
    # PostMessage(paneTray.NativeWindowHandle, WM_COMMAND, MIN_ALL, 0)
    # time.sleep(1)


def WaitHotKeyReleased(hotkey: Tuple[int, int]) -> None:
    """hotkey: Tuple[int, int], two ints tuple (modifierKey, key)"""
    mod = {
        ModifierKey.Alt: Keys.VK_MENU,
        ModifierKey.Control: Keys.VK_CONTROL,
        ModifierKey.Shift: Keys.VK_SHIFT,
        ModifierKey.Win: Keys.VK_LWIN,
    }
    while True:
        time.sleep(0.05)
        if IsKeyPressed(hotkey[1]):
            continue
        for k, v in mod.items():
            if k & hotkey[0]:
                if IsKeyPressed(v):
                    break
        else:
            break


def RunByHotKey(
    keyFunctions: Dict[Tuple[int, int], Callable],
    stopHotKey: Tuple[int, int] | None = None,
    exitHotKey: Tuple[int, int] = (ModifierKey.Control, Keys.VK_D),
    waitHotKeyReleased: bool = True,
) -> None:
    """
    Bind functions with hotkeys, the function will be run or stopped in another thread when the hotkey is pressed.
    keyFunctions: Dict[Tuple[int, int], Callable], such as {(uiautomation.ModifierKey.Control, uiautomation.Keys.VK_1) : function}
    stopHotKey: hotkey tuple
    exitHotKey: hotkey tuple
    waitHotKeyReleased: bool, if True, hotkey function will be triggered after the hotkey is released

    def main(stopEvent):
        while True:
            if stopEvent.is_set(): # must check stopEvent.is_set() if you want to stop when stop hotkey is pressed
                break
            print(n)
            n += 1
            stopEvent.wait(1)
        print('main exit')

    uiautomation.RunByHotKey({(uiautomation.ModifierKey.Control, uiautomation.Keys.VK_1) : main}
                        , (uiautomation.ModifierKey.Control | uiautomation.ModifierKey.Shift, uiautomation.Keys.VK_2))
    """
    import traceback

    def getModName(theDict, theValue):
        name = ""
        for key in theDict:
            if isinstance(theDict[key], int) and theValue & theDict[key]:
                if name:
                    name += "|"
                name += key
        return name

    def releaseAllKeys():
        for key, value in Keys.__dict__.items():
            if isinstance(value, int) and key.startswith("VK"):
                if IsKeyPressed(value):
                    ReleaseKey(value)

    def threadFunc(function, stopEvent, hotkey, hotkeyName):
        if waitHotKeyReleased:
            WaitHotKeyReleased(hotkey)
        try:
            function(stopEvent)
        except Exception as ex:
            print(traceback.format_exc())
        finally:
            releaseAllKeys()  # need to release keys if some keys were pressed

    stopHotKeyId = 1
    exitHotKeyId = 2
    hotKeyId = 3
    registed = True
    id2HotKey = {}
    id2Function = {}
    id2Thread = {}
    id2Name = {}
    for hotkey in keyFunctions:
        id2HotKey[hotKeyId] = hotkey
        id2Function[hotKeyId] = keyFunctions[hotkey]
        id2Thread[hotKeyId] = None
        modName = getModName(ModifierKey.__dict__, hotkey[0])
        keyName = _GetDictKeyName(Keys.__dict__, hotkey[1])
        id2Name[hotKeyId] = str((modName, keyName))
        if ctypes.windll.user32.RegisterHotKey(0, hotKeyId, hotkey[0], hotkey[1]):
            pass
        else:
            registed = False
        hotKeyId += 1
    if stopHotKey and len(stopHotKey) == 2:
        modName = getModName(ModifierKey.__dict__, stopHotKey[0])
        keyName = _GetDictKeyName(Keys.__dict__, stopHotKey[1])
        if ctypes.windll.user32.RegisterHotKey(0, stopHotKeyId, stopHotKey[0], stopHotKey[1]):
            pass
        else:
            registed = False
    if not registed:
        return
    if exitHotKey and len(exitHotKey) == 2:
        modName = getModName(ModifierKey.__dict__, exitHotKey[0])
        keyName = _GetDictKeyName(Keys.__dict__, exitHotKey[1])
        if ctypes.windll.user32.RegisterHotKey(0, exitHotKeyId, exitHotKey[0], exitHotKey[1]):
            pass
        else:
            pass
    funcThread = None
    livingThreads = []
    stopEvent = threading.Event()
    msg = ctypes.wintypes.MSG()
    while (
        ctypes.windll.user32.GetMessageW(
            ctypes.byref(msg), ctypes.c_void_p(0), ctypes.c_uint(0), ctypes.c_uint(0)
        )
        != 0
    ):
        if msg.message == 0x0312:  # WM_HOTKEY=0x0312
            if msg.wParam in id2HotKey:
                if (
                    msg.lParam & 0x0000FFFF == id2HotKey[msg.wParam][0]
                    and msg.lParam >> 16 & 0x0000FFFF == id2HotKey[msg.wParam][1]
                ):
                    if not id2Thread[msg.wParam]:
                        stopEvent.clear()
                        funcThread = threading.Thread(
                            None,
                            threadFunc,
                            args=(
                                id2Function[msg.wParam],
                                stopEvent,
                                id2HotKey[msg.wParam],
                                id2Name[msg.wParam],
                            ),
                        )
                        funcThread.start()
                        id2Thread[msg.wParam] = funcThread
                    else:
                        if id2Thread[msg.wParam].is_alive():
                            pass
                        else:
                            stopEvent.clear()
                            funcThread = threading.Thread(
                                None,
                                threadFunc,
                                args=(
                                    id2Function[msg.wParam],
                                    stopEvent,
                                    id2HotKey[msg.wParam],
                                    id2Name[msg.wParam],
                                ),
                            )
                            funcThread.start()
                            id2Thread[msg.wParam] = funcThread
            elif stopHotKeyId == msg.wParam:
                if (
                    msg.lParam & 0x0000FFFF == stopHotKey[0]
                    and msg.lParam >> 16 & 0x0000FFFF == stopHotKey[1]
                ):
                    stopEvent.set()
                    for id_ in id2Thread:
                        if id2Thread[id_]:
                            if id2Thread[id_].is_alive():
                                livingThreads.append((id2Thread[id_], id2Name[id_]))
                            id2Thread[id_] = None
            elif exitHotKeyId == msg.wParam:
                if (
                    msg.lParam & 0x0000FFFF == exitHotKey[0]
                    and msg.lParam >> 16 & 0x0000FFFF == exitHotKey[1]
                ):
                    stopEvent.set()
                    for id_ in id2Thread:
                        if id2Thread[id_]:
                            if id2Thread[id_].is_alive():
                                livingThreads.append((id2Thread[id_], id2Name[id_]))
                            id2Thread[id_] = None
                    break
    for thread, hotkeyName in livingThreads:
        if thread.is_alive():
            thread.join(2)
    exit()

"""
uiautomation for Python 3.
Author: yinkaisheng
Source: https://github.com/yinkaisheng/Python-UIAutomation-for-Windows

This module is for UIAutomation on Windows(Windows XP with SP3, Windows Vista and Windows 7/8/8.1/10).
It supports UIAutomation for the applications which implmented IUIAutomation, such as MFC, Windows Form, WPF, Modern UI(Metro UI), Qt, Firefox and Chrome.
Run 'automation.py -h' for help.

uiautomation is shared under the Apache Licene 2.0.
This means that the code can be freely copied and distributed, and costs nothing to use.
"""

from __future__ import annotations

import os
import sys
import time
import ctypes
import ctypes.wintypes
import comtypes
from typing import Any, List, TYPE_CHECKING
from .enums import *
from .core import *
from .core import _AutomationClient

if TYPE_CHECKING:
    from .controls import Control


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


_PatternIdInterfaces = None


def GetPatternIdInterface(patternId: int):
    """
    Get pattern COM interface by pattern id.
    patternId: int, a value in class `PatternId`.
    Return comtypes._cominterface_meta.
    """
    global _PatternIdInterfaces
    if not _PatternIdInterfaces:
        _PatternIdInterfaces = {
            # PatternId.AnnotationPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationAnnotationPattern,
            # PatternId.CustomNavigationPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationCustomNavigationPattern,
            PatternId.DockPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationDockPattern,
            # PatternId.DragPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationDragPattern,
            # PatternId.DropTargetPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationDropTargetPattern,
            PatternId.ExpandCollapsePattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationExpandCollapsePattern,
            PatternId.GridItemPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationGridItemPattern,
            PatternId.GridPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationGridPattern,
            PatternId.InvokePattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationInvokePattern,
            PatternId.ItemContainerPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationItemContainerPattern,
            PatternId.LegacyIAccessiblePattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationLegacyIAccessiblePattern,
            PatternId.MultipleViewPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationMultipleViewPattern,
            # PatternId.ObjectModelPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationObjectModelPattern,
            PatternId.RangeValuePattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationRangeValuePattern,
            PatternId.ScrollItemPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationScrollItemPattern,
            PatternId.ScrollPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationScrollPattern,
            PatternId.SelectionItemPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationSelectionItemPattern,
            PatternId.SelectionPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationSelectionPattern,
            # PatternId.SpreadsheetItemPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationSpreadsheetItemPattern,
            # PatternId.SpreadsheetPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationSpreadsheetPattern,
            # PatternId.StylesPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationStylesPattern,
            PatternId.SynchronizedInputPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationSynchronizedInputPattern,
            PatternId.TableItemPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationTableItemPattern,
            PatternId.TablePattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationTablePattern,
            # PatternId.TextChildPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationTextChildPattern,
            # PatternId.TextEditPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationTextEditPattern,
            PatternId.TextPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationTextPattern,
            # PatternId.TextPattern2: _AutomationClient.instance().UIAutomationCore.IUIAutomationTextPattern2,
            PatternId.TogglePattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationTogglePattern,
            PatternId.TransformPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationTransformPattern,
            # PatternId.TransformPattern2: _AutomationClient.instance().UIAutomationCore.IUIAutomationTransformPattern2,
            PatternId.ValuePattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationValuePattern,
            PatternId.VirtualizedItemPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationVirtualizedItemPattern,
            PatternId.WindowPattern: _AutomationClient.instance().UIAutomationCore.IUIAutomationWindowPattern,
        }
        debug = False
        # the following patterns doesn't exist on Windows 7 or lower
        try:
            _PatternIdInterfaces[PatternId.AnnotationPattern] = (
                _AutomationClient.instance().UIAutomationCore.IUIAutomationAnnotationPattern
            )
        except Exception:
            if debug:
                pass
        try:
            _PatternIdInterfaces[PatternId.CustomNavigationPattern] = (
                _AutomationClient.instance().UIAutomationCore.IUIAutomationCustomNavigationPattern
            )
        except Exception:
            if debug:
                pass
        try:
            _PatternIdInterfaces[PatternId.DragPattern] = (
                _AutomationClient.instance().UIAutomationCore.IUIAutomationDragPattern
            )
        except Exception:
            if debug:
                pass
        try:
            _PatternIdInterfaces[PatternId.DropTargetPattern] = (
                _AutomationClient.instance().UIAutomationCore.IUIAutomationDropTargetPattern
            )
        except Exception:
            if debug:
                pass
        try:
            _PatternIdInterfaces[PatternId.ObjectModelPattern] = (
                _AutomationClient.instance().UIAutomationCore.IUIAutomationObjectModelPattern
            )
        except Exception:
            if debug:
                pass
        try:
            _PatternIdInterfaces[PatternId.SpreadsheetItemPattern] = (
                _AutomationClient.instance().UIAutomationCore.IUIAutomationSpreadsheetItemPattern
            )
        except Exception:
            if debug:
                pass
        try:
            _PatternIdInterfaces[PatternId.SpreadsheetPattern] = (
                _AutomationClient.instance().UIAutomationCore.IUIAutomationSpreadsheetPattern
            )
        except Exception:
            if debug:
                pass
        try:
            _PatternIdInterfaces[PatternId.StylesPattern] = (
                _AutomationClient.instance().UIAutomationCore.IUIAutomationStylesPattern
            )
        except Exception:
            if debug:
                pass
        try:
            _PatternIdInterfaces[PatternId.SelectionPattern2] = (
                _AutomationClient.instance().UIAutomationCore.IUIAutomationSelectionPattern2
            )
        except Exception:
            if debug:
                pass
        try:
            _PatternIdInterfaces[PatternId.TextChildPattern] = (
                _AutomationClient.instance().UIAutomationCore.IUIAutomationTextChildPattern
            )
        except Exception:
            if debug:
                pass
        try:
            _PatternIdInterfaces[PatternId.TextEditPattern] = (
                _AutomationClient.instance().UIAutomationCore.IUIAutomationTextEditPattern
            )
        except Exception:
            if debug:
                pass
        try:
            _PatternIdInterfaces[PatternId.TextPattern2] = (
                _AutomationClient.instance().UIAutomationCore.IUIAutomationTextPattern2
            )
        except Exception:
            if debug:
                pass
        try:
            _PatternIdInterfaces[PatternId.TransformPattern2] = (
                _AutomationClient.instance().UIAutomationCore.IUIAutomationTransformPattern2
            )
        except Exception:
            if debug:
                pass
    return _PatternIdInterfaces[patternId]


"""
Control Pattern Mapping for UI Automation Clients.
Refer https://docs.microsoft.com/en-us/previous-versions//dd319586(v=vs.85)
"""


class AnnotationPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationannotationpattern"""
        self.pattern = pattern

    @property
    def AnnotationTypeId(self) -> int:
        """
        Property AnnotationTypeId.
        Call IUIAutomationAnnotationPattern::get_CurrentAnnotationTypeId.
        Return int, a value in class `AnnotationType`.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationannotationpattern-get_currentannotationtypeid
        """
        return self.pattern.CurrentAnnotationTypeId

    @property
    def AnnotationTypeName(self) -> str:
        """
        Property AnnotationTypeName.
        Call IUIAutomationAnnotationPattern::get_CurrentAnnotationTypeName.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationannotationpattern-get_currentannotationtypename
        """
        return self.pattern.CurrentAnnotationTypeName

    @property
    def Author(self) -> str:
        """
        Property Author.
        Call IUIAutomationAnnotationPattern::get_CurrentAuthor.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationannotationpattern-get_currentauthor
        """
        return self.pattern.CurrentAuthor

    @property
    def DateTime(self) -> str:
        """
        Property DateTime.
        Call IUIAutomationAnnotationPattern::get_CurrentDateTime.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationannotationpattern-get_currentdatetime
        """
        return self.pattern.CurrentDateTime

    @property
    def Target(self) -> "Control":
        """
        Property Target.
        Call IUIAutomationAnnotationPattern::get_CurrentTarget.
        Return `Control` subclass, the element that is being annotated.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationannotationpattern-get_currenttarget
        """
        ele = self.pattern.CurrentTarget
        return Control.CreateControlFromElement(ele)


class CustomNavigationPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationcustomnavigationpattern"""
        self.pattern = pattern

    def Navigate(self, direction: int) -> "Control":
        """
        Call IUIAutomationCustomNavigationPattern::Navigate.
        Get the next control in the specified direction within the logical UI tree.
        direction: int, a value in class `NavigateDirection`.
        Return `Control` subclass or None.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationcustomnavigationpattern-navigate
        """
        ele = self.pattern.Navigate(direction)
        return Control.CreateControlFromElement(ele)


class DockPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationdockpattern"""
        self.pattern = pattern

    @property
    def DockPosition(self) -> int:
        """
        Property DockPosition.
        Call IUIAutomationDockPattern::get_CurrentDockPosition.
        Return int, a value in class `DockPosition`.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationdockpattern-get_currentdockposition
        """
        return self.pattern.CurrentDockPosition

    def SetDockPosition(self, dockPosition: int, waitTime: float = OPERATION_WAIT_TIME) -> int:
        """
        Call IUIAutomationDockPattern::SetDockPosition.
        dockPosition: int, a value in class `DockPosition`.
        waitTime: float.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationdockpattern-setdockposition
        """
        ret = self.pattern.SetDockPosition(dockPosition)
        time.sleep(waitTime)
        return ret


class DragPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationdragpattern"""
        self.pattern = pattern

    @property
    def DropEffect(self) -> str:
        """
        Property DropEffect.
        Call IUIAutomationDragPattern::get_CurrentDropEffect.
        Return str, a localized string that indicates what happens
                    when the user drops this element as part of a drag-drop operation.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationdragpattern-get_currentdropeffect
        """
        return self.pattern.CurrentDropEffect

    @property
    def DropEffects(self) -> List[str]:
        """
        Property DropEffects.
        Call IUIAutomationDragPattern::get_CurrentDropEffects, todo SAFEARRAY.
        Return List[str], a list of localized strings that enumerate the full set of effects
                     that can happen when this element as part of a drag-and-drop operation.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationdragpattern-get_currentdropeffects
        """
        return self.pattern.CurrentDropEffects

    @property
    def IsGrabbed(self) -> bool:
        """
        Property IsGrabbed.
        Call IUIAutomationDragPattern::get_CurrentIsGrabbed.
        Return bool, indicates whether the user has grabbed this element as part of a drag-and-drop operation.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationdragpattern-get_currentisgrabbed
        """
        return bool(self.pattern.CurrentIsGrabbed)

    def GetGrabbedItems(self) -> List["Control"]:
        """
        Call IUIAutomationDragPattern::GetCurrentGrabbedItems.
        Return List[Control], a list of `Control` subclasses that represent the full set of items
                     that the user is dragging as part of a drag operation.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationdragpattern-getcurrentgrabbeditems
        """
        eleArray = self.pattern.GetCurrentGrabbedItems()
        if eleArray:
            controls = []
            for i in range(eleArray.Length):
                ele = eleArray.GetElement(i)
                con = Control.CreateControlFromElement(element=ele)
                if con:
                    controls.append(con)
            return controls
        return []


class DropTargetPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationdroptargetpattern"""
        self.pattern = pattern

    @property
    def DropTargetEffect(self) -> str:
        """
        Property DropTargetEffect.
        Call IUIAutomationDropTargetPattern::get_CurrentDropTargetEffect.
        Return str, a localized string that describes what happens
                    when the user drops the grabbed element on this drop target.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationdragpattern-get_currentdroptargeteffect
        """
        return self.pattern.CurrentDropTargetEffect

    @property
    def DropTargetEffects(self) -> List[str]:
        """
        Property DropTargetEffects.
        Call IUIAutomationDropTargetPattern::get_CurrentDropTargetEffects, todo SAFEARRAY.
        Return List[str], a list of localized strings that enumerate the full set of effects
                     that can happen when the user drops a grabbed element on this drop target
                     as part of a drag-and-drop operation.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationdragpattern-get_currentdroptargeteffects
        """
        return self.pattern.CurrentDropTargetEffects


class ExpandCollapsePattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationexpandcollapsepattern"""
        self.pattern = pattern

    @property
    def ExpandCollapseState(self) -> int:
        """
        Property ExpandCollapseState.
        Call IUIAutomationExpandCollapsePattern::get_CurrentExpandCollapseState.
        Return int, a value in class ExpandCollapseState.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationexpandcollapsepattern-get_currentexpandcollapsestate
        """
        return self.pattern.CurrentExpandCollapseState

    def Collapse(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationExpandCollapsePattern::Collapse.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationexpandcollapsepattern-collapse
        """
        try:
            ret = self.pattern.Collapse() == S_OK
            time.sleep(waitTime)
            return ret
        except Exception:
            pass
        return False

    def Expand(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationExpandCollapsePattern::Expand.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationexpandcollapsepattern-expand
        """
        try:
            ret = self.pattern.Expand() == S_OK
            time.sleep(waitTime)
            return ret
        except Exception:
            pass
        return False


class GridItemPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationgriditempattern"""
        self.pattern = pattern

    @property
    def Column(self) -> int:
        """
        Property Column.
        Call IUIAutomationGridItemPattern::get_CurrentColumn.
        Return int, the zero-based index of the column that contains the item.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationgriditempattern-get_currentcolumn
        """
        return self.pattern.CurrentColumn

    @property
    def ColumnSpan(self) -> int:
        """
        Property ColumnSpan.
        Call IUIAutomationGridItemPattern::get_CurrentColumnSpan.
        Return int, the number of columns spanned by the grid item.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationgriditempattern-get_currentcolumnspan
        """
        return self.pattern.CurrentColumnSpan

    @property
    def ContainingGrid(self) -> "Control":
        """
        Property ContainingGrid.
        Call IUIAutomationGridItemPattern::get_CurrentContainingGrid.
        Return `Control` subclass, the element that contains the grid item.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationgriditempattern-get_currentcontaininggrid
        """
        return Control.CreateControlFromElement(self.pattern.CurrentContainingGrid)

    @property
    def Row(self) -> int:
        """
        Property Row.
        Call IUIAutomationGridItemPattern::get_CurrentRow.
        Return int, the zero-based index of the row that contains the grid item.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationgriditempattern-get_currentrow
        """
        return self.pattern.CurrentRow

    @property
    def RowSpan(self) -> int:
        """
        Property RowSpan.
        Call IUIAutomationGridItemPattern::get_CurrentRowSpan.
        Return int, the number of rows spanned by the grid item.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationgriditempattern-get_currentrowspan
        """
        return self.pattern.CurrentRowSpan


class GridPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationgridpattern"""
        self.pattern = pattern

    @property
    def ColumnCount(self) -> int:
        """
        Property ColumnCount.
        Call IUIAutomationGridPattern::get_CurrentColumnCount.
        Return int, the number of columns in the grid.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationgridpattern-get_currentcolumncount
        """
        return self.pattern.CurrentColumnCount

    @property
    def RowCount(self) -> int:
        """
        Property RowCount.
        Call IUIAutomationGridPattern::get_CurrentRowCount.
        Return int, the number of rows in the grid.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationgridpattern-get_currentrowcount
        """
        return self.pattern.CurrentRowCount

    def GetItem(self, row: int, column: int) -> "Control":
        """
        Call IUIAutomationGridPattern::GetItem.
        Return `Control` subclass, a control representing an item in the grid.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationgridpattern-getitem
        """
        return Control.CreateControlFromElement(self.pattern.GetItem(row, column))


class InvokePattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationinvokepattern"""
        self.pattern = pattern

    def Invoke(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationInvokePattern::Invoke.
        Invoke the action of a control, such as a button click.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationinvokepattern-invoke
        """
        ret = self.pattern.Invoke() == S_OK
        time.sleep(waitTime)
        return ret


class ItemContainerPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationitemcontainerpattern"""
        self.pattern = pattern

    def FindItemByProperty(self, control: "Control", propertyId: int, propertyValue) -> "Control":
        """
        Call IUIAutomationItemContainerPattern::FindItemByProperty.
        control: `Control` or its subclass.
        propertyValue: COM VARIANT according to propertyId? todo.
        propertyId: int, a value in class `PropertyId`.
        Return `Control` subclass, a control within a containing element, based on a specified property value.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationitemcontainerpattern-finditembyproperty
        """
        ele = self.pattern.FindItemByProperty(control.Element, propertyId, propertyValue)
        return Control.CreateControlFromElement(ele)


class LegacyIAccessiblePattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationlegacyiaccessiblepattern"""
        self.pattern = pattern

    @property
    def ChildId(self) -> int:
        """
        Property ChildId.
        Call IUIAutomationLegacyIAccessiblePattern::get_CurrentChildId.
        Return int, the Microsoft Active Accessibility child identifier for the element.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-get_currentchildid
        """
        return self.pattern.CurrentChildId

    @property
    def DefaultAction(self) -> str:
        """
        Property DefaultAction.
        Call IUIAutomationLegacyIAccessiblePattern::get_CurrentDefaultAction.
        Return str, the Microsoft Active Accessibility current default action for the element.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-get_currentdefaultaction
        """
        return self.pattern.CurrentDefaultAction

    @property
    def Description(self) -> str:
        """
        Property Description.
        Call IUIAutomationLegacyIAccessiblePattern::get_CurrentDescription.
        Return str, the Microsoft Active Accessibility description of the element.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-get_currentdescription
        """
        return self.pattern.CurrentDescription

    @property
    def Help(self) -> str:
        """
        Property Help.
        Call IUIAutomationLegacyIAccessiblePattern::get_CurrentHelp.
        Return str, the Microsoft Active Accessibility help string for the element.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-get_currenthelp
        """
        return self.pattern.CurrentHelp

    @property
    def KeyboardShortcut(self) -> str:
        """
        Property KeyboardShortcut.
        Call IUIAutomationLegacyIAccessiblePattern::get_CurrentKeyboardShortcut.
        Return str, the Microsoft Active Accessibility keyboard shortcut property for the element.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-get_currentkeyboardshortcut
        """
        return self.pattern.CurrentKeyboardShortcut

    @property
    def Name(self) -> str:
        """
        Property Name.
        Call IUIAutomationLegacyIAccessiblePattern::get_CurrentName.
        Return str, the Microsoft Active Accessibility name property of the element.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-get_currentname
        """
        return self.pattern.CurrentName or ""  # CurrentName may be None

    @property
    def Role(self) -> int:
        """
        Property Role.
        Call IUIAutomationLegacyIAccessiblePattern::get_CurrentRole.
        Return int, a value in calss `AccessibleRole`, the Microsoft Active Accessibility role identifier.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-get_currentrole
        """
        return self.pattern.CurrentRole

    @property
    def State(self) -> int:
        """
        Property State.
        Call IUIAutomationLegacyIAccessiblePattern::get_CurrentState.
        Return int, a value in calss `AccessibleState`, the Microsoft Active Accessibility state identifier.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-get_currentstate
        """
        return self.pattern.CurrentState

    @property
    def Value(self) -> str:
        """
        Property Value.
        Call IUIAutomationLegacyIAccessiblePattern::get_CurrentValue.
        Return str, the Microsoft Active Accessibility value property.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-get_currentvalue
        """
        return self.pattern.CurrentValue

    def DoDefaultAction(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationLegacyIAccessiblePattern::DoDefaultAction.
        Perform the Microsoft Active Accessibility default action for the element.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-dodefaultaction
        """
        ret = self.pattern.DoDefaultAction() == S_OK
        time.sleep(waitTime)
        return ret

    def GetSelection(self) -> List["Control"]:
        """
        Call IUIAutomationLegacyIAccessiblePattern::GetCurrentSelection.
        Return List[Control], a list of `Control` subclasses,
                     the Microsoft Active Accessibility property that identifies the selected children of this element.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-getcurrentselection
        """
        eleArray = self.pattern.GetCurrentSelection()
        if eleArray:
            controls = []
            for i in range(eleArray.Length):
                ele = eleArray.GetElement(i)
                con = Control.CreateControlFromElement(element=ele)
                if con:
                    controls.append(con)
            return controls
        return []

    def GetIAccessible(self):
        """
        Call IUIAutomationLegacyIAccessiblePattern::GetIAccessible, todo.
        Return an IAccessible object that corresponds to the Microsoft UI Automation element.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-getiaccessible
        Refer https://docs.microsoft.com/en-us/windows/win32/api/oleacc/nn-oleacc-iaccessible
        """
        return self.pattern.GetIAccessible()

    def Select(self, flagsSelect: int, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationLegacyIAccessiblePattern::Select.
        Perform a Microsoft Active Accessibility selection.
        flagsSelect: int, a value in `AccessibleSelection`.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-select
        """
        ret = self.pattern.Select(flagsSelect) == S_OK
        time.sleep(waitTime)
        return ret

    def SetValue(self, value: str, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationLegacyIAccessiblePattern::SetValue.
        Set the Microsoft Active Accessibility value property for the element.
        value: str.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-setvalue
        """
        ret = self.pattern.SetValue(value) == S_OK
        time.sleep(waitTime)
        return ret


class MultipleViewPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationmultipleviewpattern"""
        self.pattern = pattern

    @property
    def CurrentView(self) -> int:
        """
        Property CurrentView.
        Call IUIAutomationMultipleViewPattern::get_CurrentCurrentView.
        Return int, the control-specific identifier of the current view of the control.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationmultipleviewpattern-get_currentcurrentview
        """
        return self.pattern.CurrentCurrentView

    def GetSupportedViews(self) -> List[int]:
        """
        Call IUIAutomationMultipleViewPattern::GetCurrentSupportedViews, todo.
        Return List[int], a list of int, control-specific view identifiers.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationmultipleviewpattern-getcurrentsupportedviews
        """
        return self.pattern.GetCurrentSupportedViews()

    def GetViewName(self, view: int) -> str:
        """
        Call IUIAutomationMultipleViewPattern::GetViewName.
        view: int, the control-specific view identifier.
        Return str, the name of a control-specific view.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationmultipleviewpattern-getviewname
        """
        return self.pattern.GetViewName(view)

    def SetView(self, view: int) -> bool:
        """
        Call IUIAutomationMultipleViewPattern::SetCurrentView.
        Set the view of the control.
        view: int, the control-specific view identifier.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationmultipleviewpattern-setcurrentview
        """
        return self.pattern.SetCurrentView(view) == S_OK


class ObjectModelPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationobjectmodelpattern"""
        self.pattern = pattern

    # def GetUnderlyingObjectModel(self) -> ctypes.POINTER(comtypes.automation.IUnknown):
    #     """
    #     Call IUIAutomationObjectModelPattern::GetUnderlyingObjectModel, todo.
    #     Return `ctypes.POINTER(comtypes.IUnknown)`, an interface used to access the underlying object model of the provider.
    #     Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationobjectmodelpattern-getunderlyingobjectmodel
    #     """
    #     return self.pattern.GetUnderlyingObjectModel()


class RangeValuePattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationrangevaluepattern"""
        self.pattern = pattern

    @property
    def IsReadOnly(self) -> bool:
        """
        Property IsReadOnly.
        Call IUIAutomationRangeValuePattern::get_CurrentIsReadOnly.
        Return bool, indicates whether the value of the element can be changed.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationrangevaluepattern-get_currentisreadonly
        """
        return bool(self.pattern.CurrentIsReadOnly)

    @property
    def LargeChange(self) -> float:
        """
        Property LargeChange.
        Call IUIAutomationRangeValuePattern::get_CurrentLargeChange.
        Return float, the value that is added to or subtracted from the value of the control
                      when a large change is made, such as when the PAGE DOWN key is pressed.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationrangevaluepattern-get_currentlargechange
        """
        return self.pattern.CurrentLargeChange

    @property
    def Maximum(self) -> float:
        """
        Property Maximum.
        Call IUIAutomationRangeValuePattern::get_CurrentMaximum.
        Return float, the maximum value of the control.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationrangevaluepattern-get_currentmaximum
        """
        return self.pattern.CurrentMaximum

    @property
    def Minimum(self) -> float:
        """
        Property Minimum.
        Call IUIAutomationRangeValuePattern::get_CurrentMinimum.
        Return float, the minimum value of the control.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationrangevaluepattern-get_currentminimum
        """
        return self.pattern.CurrentMinimum

    @property
    def SmallChange(self) -> float:
        """
        Property SmallChange.
        Call IUIAutomationRangeValuePattern::get_CurrentSmallChange.
        Return float, the value that is added to or subtracted from the value of the control
                      when a small change is made, such as when an arrow key is pressed.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationrangevaluepattern-get_currentsmallchange
        """
        return self.pattern.CurrentSmallChange

    @property
    def Value(self) -> float:
        """
        Property Value.
        Call IUIAutomationRangeValuePattern::get_CurrentValue.
        Return float, the value of the control.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationrangevaluepattern-get_currentvalue
        """
        return self.pattern.CurrentValue

    def SetValue(self, value: float, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationRangeValuePattern::SetValue.
        Set the value of the control.
        value: int.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationrangevaluepattern-setvalue
        """
        ret = self.pattern.SetValue(value) == S_OK
        time.sleep(waitTime)
        return ret


class ScrollItemPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationscrollitempattern"""
        self.pattern = pattern

    def ScrollIntoView(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationScrollItemPattern::ScrollIntoView.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationscrollitempattern-scrollintoview
        """
        ret = self.pattern.ScrollIntoView() == S_OK
        time.sleep(waitTime)
        return ret


class ScrollPattern:
    NoScrollValue = -1

    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationscrollpattern"""
        self.pattern = pattern

    @property
    def HorizontallyScrollable(self) -> bool:
        """
        Property HorizontallyScrollable.
        Call IUIAutomationScrollPattern::get_CurrentHorizontallyScrollable.
        Return bool, indicates whether the element can scroll horizontally.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationscrollpattern-get_currenthorizontallyscrollable
        """
        return bool(self.pattern.CurrentHorizontallyScrollable)

    @property
    def HorizontalScrollPercent(self) -> float:
        """
        Property HorizontalScrollPercent.
        Call IUIAutomationScrollPattern::get_CurrentHorizontalScrollPercent.
        Return float, the horizontal scroll position.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationscrollpattern-get_currenthorizontalscrollpercent
        """
        return self.pattern.CurrentHorizontalScrollPercent

    @property
    def HorizontalViewSize(self) -> float:
        """
        Property HorizontalViewSize.
        Call IUIAutomationScrollPattern::get_CurrentHorizontalViewSize.
        Return float, the horizontal size of the viewable region of a scrollable element.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationscrollpattern-get_currenthorizontalviewsize
        """
        return self.pattern.CurrentHorizontalViewSize

    @property
    def VerticallyScrollable(self) -> bool:
        """
        Property VerticallyScrollable.
        Call IUIAutomationScrollPattern::get_CurrentVerticallyScrollable.
        Return bool, indicates whether the element can scroll vertically.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationscrollpattern-get_currentverticallyscrollable
        """
        return bool(self.pattern.CurrentVerticallyScrollable)

    @property
    def VerticalScrollPercent(self) -> float:
        """
        Property VerticalScrollPercent.
        Call IUIAutomationScrollPattern::get_CurrentVerticalScrollPercent.
        Return float, the vertical scroll position.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationscrollpattern-get_currentverticalscrollpercent
        """
        return self.pattern.CurrentVerticalScrollPercent

    @property
    def VerticalViewSize(self) -> float:
        """
        Property VerticalViewSize.
        Call IUIAutomationScrollPattern::get_CurrentVerticalViewSize.
        Return float, the vertical size of the viewable region of a scrollable element.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationscrollpattern-get_currentverticalviewsize
        """
        return self.pattern.CurrentVerticalViewSize

    def Scroll(
        self,
        horizontalAmount: int,
        verticalAmount: int,
        waitTime: float = OPERATION_WAIT_TIME,
    ) -> bool:
        """
        Call IUIAutomationScrollPattern::Scroll.
        Scroll the visible region of the content area horizontally and vertically.
        horizontalAmount: int, a value in ScrollAmount.
        verticalAmount: int, a value in ScrollAmount.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationscrollpattern-scroll
        """
        ret = self.pattern.Scroll(horizontalAmount, verticalAmount) == S_OK
        time.sleep(waitTime)
        return ret

    def SetScrollPercent(
        self,
        horizontalPercent: float,
        verticalPercent: float,
        waitTime: float = OPERATION_WAIT_TIME,
    ) -> bool:
        """
        Call IUIAutomationScrollPattern::SetScrollPercent.
        Set the horizontal and vertical scroll positions as a percentage of the total content area within the UI Automation element.
        horizontalPercent: float or int, a value in [0, 100] or ScrollPattern.NoScrollValue(-1) if no scroll.
        verticalPercent: float or int, a value  in [0, 100] or ScrollPattern.NoScrollValue(-1) if no scroll.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationscrollpattern-setscrollpercent
        """
        ret = self.pattern.SetScrollPercent(horizontalPercent, verticalPercent) == S_OK
        time.sleep(waitTime)
        return ret


class SelectionItemPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationselectionitempattern"""
        self.pattern = pattern

    def AddToSelection(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationSelectionItemPattern::AddToSelection.
        Add the current element to the collection of selected items.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationselectionitempattern-addtoselection
        """
        ret = self.pattern.AddToSelection() == S_OK
        time.sleep(waitTime)
        return ret

    @property
    def IsSelected(self) -> bool:
        """
        Property IsSelected.
        Call IUIAutomationScrollPattern::get_CurrentIsSelected.
        Return bool, indicates whether this item is selected.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationscrollpattern-get_currentisselected
        """
        return bool(self.pattern.CurrentIsSelected)

    @property
    def SelectionContainer(self) -> "Control":
        """
        Property SelectionContainer.
        Call IUIAutomationScrollPattern::get_CurrentSelectionContainer.
        Return `Control` subclass, the element that supports IUIAutomationSelectionPattern and acts as the container for this item.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationscrollpattern-get_currentselectioncontainer
        """
        ele = self.pattern.CurrentSelectionContainer
        return Control.CreateControlFromElement(ele)

    def RemoveFromSelection(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationSelectionItemPattern::RemoveFromSelection.
        Remove this element from the selection.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationselectionitempattern-removefromselection
        """
        ret = self.pattern.RemoveFromSelection() == S_OK
        time.sleep(waitTime)
        return ret

    def Select(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationSelectionItemPattern::Select.
        Clear any selected items and then select the current element.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationselectionitempattern-select
        """
        ret = self.pattern.Select() == S_OK
        time.sleep(waitTime)
        return ret


class SelectionPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationselectionpattern"""
        self.pattern = pattern

    @property
    def CanSelectMultiple(self) -> bool:
        """
        Property CanSelectMultiple.
        Call IUIAutomationSelectionPattern::get_CurrentCanSelectMultiple.
        Return bool, indicates whether more than one item in the container can be selected at one time.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationselectionpattern-get_currentcanselectmultiple
        """
        return bool(self.pattern.CurrentCanSelectMultiple)

    @property
    def IsSelectionRequired(self) -> bool:
        """
        Property IsSelectionRequired.
        Call IUIAutomationSelectionPattern::get_CurrentIsSelectionRequired.
        Return bool, indicates whether at least one item must be selected at all times.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationselectionpattern-get_currentisselectionrequired
        """
        return bool(self.pattern.CurrentIsSelectionRequired)

    def GetSelection(self) -> List["Control"]:
        """
        Call IUIAutomationSelectionPattern::GetCurrentSelection.
        Return List[Control], a list of `Control` subclasses, the selected elements in the container..
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationselectionpattern-getcurrentselection
        """
        eleArray = self.pattern.GetCurrentSelection()
        if eleArray:
            controls = []
            for i in range(eleArray.Length):
                ele = eleArray.GetElement(i)
                con = Control.CreateControlFromElement(element=ele)
                if con:
                    controls.append(con)
            return controls
        return []


class SelectionPattern2(SelectionPattern):
    """
    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationselectionpattern2
    """

    def __init__(self, pattern=None):
        super().__init__(pattern)

    @property
    def CurrentSelectedItem(self):
        """
        Property CurrentSelectedItem.
        Call IUIAutomationSelectionPattern2::get_CurrentCurrentSelectedItem.
        Return Control subclass, the currently selected element.
        """
        ele = self.pattern.CurrentCurrentSelectedItem
        return Control.CreateControlFromElement(element=ele) if ele else None

    @property
    def FirstSelectedItem(self):
        """
        Property FirstSelectedItem.
        Call IUIAutomationSelectionPattern2::get_CurrentFirstSelectedItem.
        Return Control subclass, the currently selected element.
        """
        ele = self.pattern.CurrentFirstSelectedItem
        return Control.CreateControlFromElement(element=ele) if ele else None

    @property
    def LastSelectedItem(self):
        """
        Property LastSelectedItem.
        Call IUIAutomationSelectionPattern2::get_CurrentLastSelectedItem.
        Return Control subclass, the currently selected element.
        """
        ele = self.pattern.CurrentLastSelectedItem
        return Control.CreateControlFromElement(element=ele) if ele else None

    @property
    def ItemCount(self) -> int:
        """
        Property ItemCount.
        Call IUIAutomationSelectionPattern2::get_CurrentItemCount.
        """
        return self.pattern.CurrentItemCount


class SpreadsheetItemPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationspreadsheetitempattern"""
        self.pattern = pattern

    @property
    def Formula(self) -> str:
        """
        Property Formula.
        Call IUIAutomationSpreadsheetItemPattern::get_CurrentFormula.
        Return str, the formula for this cell.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationspreadsheetitempattern-get_currentformula
        """
        return self.pattern.CurrentFormula

    def GetAnnotationObjects(self) -> List["Control"]:
        """
        Call IUIAutomationSelectionPattern::GetCurrentAnnotationObjects.
        Return List[Control], a list of `Control` subclasses representing the annotations associated with this spreadsheet cell.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationspreadsheetitempattern-getcurrentannotationobjects
        """
        eleArray = self.pattern.GetCurrentAnnotationObjects()
        if eleArray:
            controls = []
            for i in range(eleArray.Length):
                ele = eleArray.GetElement(i)
                con = Control.CreateControlFromElement(element=ele)
                if con:
                    controls.append(con)
            return controls
        return []

    def GetAnnotationTypes(self) -> List[int]:
        """
        Call IUIAutomationSelectionPattern::GetCurrentAnnotationTypes.
        Return List[int], a list of int values in class `AnnotationType`,
                     indicating the types of annotations that are associated with this spreadsheet cell.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationselectionpattern-getcurrentannotationtypes
        """
        return self.pattern.GetCurrentAnnotationTypes()


class SpreadsheetPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationspreadsheetpattern"""
        self.pattern = pattern

    def GetItemByName(self, name: str) -> "Control":
        """
        Call IUIAutomationSpreadsheetPattern::GetItemByName.
        name: str.
        Return `Control` subclass or None, represents the spreadsheet cell that has the specified name..
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationspreadsheetpattern-getitembyname
        """
        ele = self.pattern.GetItemByName(name)
        return Control.CreateControlFromElement(element=ele)


class StylesPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationstylespattern"""
        self.pattern = pattern

    @property
    def ExtendedProperties(self) -> str:
        """
        Property ExtendedProperties.
        Call IUIAutomationStylesPattern::get_CurrentExtendedProperties.
        Return str, a localized string that contains the list of extended properties for an element in a document.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationstylespattern-get_currentextendedproperties
        """
        return self.pattern.CurrentExtendedProperties

    @property
    def FillColor(self) -> int:
        """
        Property FillColor.
        Call IUIAutomationStylesPattern::get_CurrentFillColor.
        Return int, the fill color of an element in a document.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationstylespattern-get_currentfillcolor
        """
        return self.pattern.CurrentFillColor

    @property
    def FillPatternColor(self) -> int:
        """
        Property FillPatternColor.
        Call IUIAutomationStylesPattern::get_CurrentFillPatternColor.
        Return int, the color of the pattern used to fill an element in a document.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationstylespattern-get_currentfillpatterncolor
        """
        return self.pattern.CurrentFillPatternColor

    @property
    def Shape(self) -> str:
        """
        Property Shape.
        Call IUIAutomationStylesPattern::get_CurrentShape.
        Return str, the shape of an element in a document.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationstylespattern-get_currentshape
        """
        return self.pattern.CurrentShape

    @property
    def StyleId(self) -> int:
        """
        Property StyleId.
        Call IUIAutomationStylesPattern::get_CurrentStyleId.
        Return int, a value in class `StyleId`, the visual style associated with an element in a document.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationstylespattern-get_currentstyleid
        """
        return self.pattern.CurrentStyleId

    @property
    def StyleName(self) -> str:
        """
        Property StyleName.
        Call IUIAutomationStylesPattern::get_CurrentStyleName.
        Return str, the name of the visual style associated with an element in a document.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationstylespattern-get_currentstylename
        """
        return self.pattern.CurrentStyleName


class SynchronizedInputPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationsynchronizedinputpattern"""
        self.pattern = pattern

    def Cancel(self) -> bool:
        """
        Call IUIAutomationSynchronizedInputPattern::Cancel.
        Cause the Microsoft UI Automation provider to stop listening for mouse or keyboard input.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationsynchronizedinputpattern-cancel
        """
        return self.pattern.Cancel() == S_OK

    def StartListening(self) -> bool:
        """
        Call IUIAutomationSynchronizedInputPattern::StartListening.
        Cause the Microsoft UI Automation provider to start listening for mouse or keyboard input.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationsynchronizedinputpattern-startlistening
        """
        return self.pattern.StartListening() == S_OK


class TableItemPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationtableitempattern"""
        self.pattern = pattern

    def GetColumnHeaderItems(self) -> List["Control"]:
        """
        Call IUIAutomationTableItemPattern::GetCurrentColumnHeaderItems.
        Return List[Control], a list of `Control` subclasses, the column headers associated with a table item or cell.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtableitempattern-getcurrentcolumnheaderitems
        """
        eleArray = self.pattern.GetCurrentColumnHeaderItems()
        if eleArray:
            controls = []
            for i in range(eleArray.Length):
                ele = eleArray.GetElement(i)
                con = Control.CreateControlFromElement(element=ele)
                if con:
                    controls.append(con)
            return controls
        return []

    def GetRowHeaderItems(self) -> List["Control"]:
        """
        Call IUIAutomationTableItemPattern::GetCurrentRowHeaderItems.
        Return List[Control], a list of `Control` subclasses, the row headers associated with a table item or cell.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtableitempattern-getcurrentrowheaderitems
        """
        eleArray = self.pattern.GetCurrentRowHeaderItems()
        if eleArray:
            controls = []
            for i in range(eleArray.Length):
                ele = eleArray.GetElement(i)
                con = Control.CreateControlFromElement(element=ele)
                if con:
                    controls.append(con)
            return controls
        return []


class TablePattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationtablepattern"""
        self.pattern = pattern

    @property
    def RowOrColumnMajor(self) -> int:
        """
        Property RowOrColumnMajor.
        Call IUIAutomationTablePattern::get_CurrentRowOrColumnMajor.
        Return int, a value in class `RowOrColumnMajor`, the primary direction of traversal for the table.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtablepattern-get_currentroworcolumnmajor
        """
        return self.pattern.CurrentRowOrColumnMajor

    def GetColumnHeaders(self) -> List["Control"]:
        """
        Call IUIAutomationTablePattern::GetCurrentColumnHeaders.
        Return List[Control], a list of `Control` subclasses, representing all the column headers in a table..
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtablepattern-getcurrentcolumnheaders
        """
        eleArray = self.pattern.GetCurrentColumnHeaders()
        if eleArray:
            controls = []
            for i in range(eleArray.Length):
                ele = eleArray.GetElement(i)
                con = Control.CreateControlFromElement(element=ele)
                if con:
                    controls.append(con)
            return controls
        return []

    def GetRowHeaders(self) -> List["Control"]:
        """
        Call IUIAutomationTablePattern::GetCurrentRowHeaders.
        Return List[Control], a list of `Control` subclasses, representing all the row headers in a table.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtablepattern-getcurrentrowheaders
        """
        eleArray = self.pattern.GetCurrentRowHeaders()
        if eleArray:
            controls = []
            for i in range(eleArray.Length):
                ele = eleArray.GetElement(i)
                con = Control.CreateControlFromElement(element=ele)
                if con:
                    controls.append(con)
            return controls
        return []


class TextRange:
    def __init__(self, textRange=None):
        """
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationtextrange
        """
        self.textRange = textRange

    def AddToSelection(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationTextRange::AddToSelection.
        Add the text range to the collection of selected text ranges in a control that supports multiple, disjoint spans of selected text.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-addtoselection
        """
        ret = self.textRange.AddToSelection() == S_OK
        time.sleep(waitTime)
        return ret

    def Clone(self) -> "TextRange":
        """
        Call IUIAutomationTextRange::Clone.
        return `TextRange`, identical to the original and inheriting all properties of the original.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-clone
        """
        return TextRange(textRange=self.textRange.Clone())

    def Compare(self, textRange: "TextRange") -> bool:
        """
        Call IUIAutomationTextRange::Compare.
        textRange: `TextRange`.
        Return bool, specifies whether this text range has the same endpoints as another text range.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-compare
        """
        return bool(self.textRange.Compare(textRange.textRange))

    def CompareEndpoints(
        self, srcEndPoint: int, textRange: "TextRange", targetEndPoint: int
    ) -> int:
        """
        Call IUIAutomationTextRange::CompareEndpoints.
        srcEndPoint: int, a value in class `TextPatternRangeEndpoint`.
        textRange: `TextRange`.
        targetEndPoint: int, a value in class `TextPatternRangeEndpoint`.
        Return int, a negative value if the caller's endpoint occurs earlier in the text than the target endpoint;
                    0 if the caller's endpoint is at the same location as the target endpoint;
                    or a positive value if the caller's endpoint occurs later in the text than the target endpoint.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-compareendpoints
        """
        return self.textRange.CompareEndpoints(srcEndPoint, textRange.textRange, targetEndPoint)

    def ExpandToEnclosingUnit(self, unit: int, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationTextRange::ExpandToEnclosingUnit.
        Normalize the text range by the specified text unit.
            The range is expanded if it is smaller than the specified unit,
            or shortened if it is longer than the specified unit.
        unit: int, a value in class `TextUnit`.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-expandtoenclosingunit
        """
        ret = self.textRange.ExpandToEnclosingUnit(unit) == S_OK
        time.sleep(waitTime)
        return ret

    def FindAttribute(self, textAttributeId: int, val, backward: bool) -> "TextRange" | None:
        """
        Call IUIAutomationTextRange::FindAttribute.
        textAttributeID: int, a value in class `TextAttributeId`.
        val: COM VARIANT according to textAttributeId? todo.
        backward: bool, True if the last occurring text range should be returned instead of the first; otherwise False.
        return `TextRange` or None, a text range subset that has the specified text attribute value.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-findattribute
        """
        textRange = self.textRange.FindAttribute(textAttributeId, val, int(backward))
        if textRange:
            return TextRange(textRange=textRange)
        return None

    def FindText(self, text: str, backward: bool, ignoreCase: bool) -> "TextRange" | None:
        """
        Call IUIAutomationTextRange::FindText.
        text: str,
        backward: bool, True if the last occurring text range should be returned instead of the first; otherwise False.
        ignoreCase: bool, True if case should be ignored; otherwise False.
        return `TextRange` or None, a text range subset that contains the specified text.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-findtext
        """
        textRange = self.textRange.FindText(text, int(backward), int(ignoreCase))
        if textRange:
            return TextRange(textRange=textRange)
        return None

    def GetAttributeValue(self, textAttributeId: int) -> ctypes.POINTER(comtypes.IUnknown):
        """
        Call IUIAutomationTextRange::GetAttributeValue.
        textAttributeId: int, a value in class `TextAttributeId`.
        Return `ctypes.POINTER(comtypes.IUnknown)` or None, the value of the specified text attribute across the entire text range, todo.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-getattributevalue
        """
        return self.textRange.GetAttributeValue(textAttributeId)

    def GetBoundingRectangles(self) -> List[Rect]:
        """
        Call IUIAutomationTextRange::GetBoundingRectangles.
        textAttributeId: int, a value in class `TextAttributeId`.
        Return List[Rect], a list of `Rect`.
            bounding rectangles for each fully or partially visible line of text in a text range..
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-getboundingrectangles

        for rect in textRange.GetBoundingRectangles():
            print(rect.left, rect.top, rect.right, rect.bottom, rect.width(), rect.height(), rect.xcenter(), rect.ycenter())
        """
        floats = self.textRange.GetBoundingRectangles()
        rects = []
        for i in range(len(floats) // 4):
            rect = Rect(
                int(floats[i * 4]),
                int(floats[i * 4 + 1]),
                int(floats[i * 4]) + int(floats[i * 4 + 2]),
                int(floats[i * 4 + 1]) + int(floats[i * 4 + 3]),
            )
            rects.append(rect)
        return rects

    def GetChildren(self) -> List["Control"]:
        """
        Call IUIAutomationTextRange::GetChildren.
        textAttributeId: int, a value in class `TextAttributeId`.
        Return List[Control], a list of `Control` subclasses, embedded objects that fall within the text range..
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-getchildren
        """
        eleArray = self.textRange.GetChildren()
        if eleArray:
            controls = []
            for i in range(eleArray.Length):
                ele = eleArray.GetElement(i)
                con = Control.CreateControlFromElement(element=ele)
                if con:
                    controls.append(con)
            return controls
        return []

    def GetEnclosingControl(self) -> "Control":
        """
        Call IUIAutomationTextRange::GetEnclosingElement.
        Return `Control` subclass, the innermost UI Automation element that encloses the text range.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-getenclosingelement
        """
        return Control.CreateControlFromElement(self.textRange.GetEnclosingElement())

    def GetText(self, maxLength: int = -1) -> str:
        """
        Call IUIAutomationTextRange::GetText.
        maxLength: int, the maximum length of the string to return, or -1 if no limit is required.
        Return str, the plain text of the text range. A provider that yields no
            text is normalized to an empty string so callers can treat the
            result as a str unconditionally.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-gettext
        """
        return self.textRange.GetText(maxLength) or ""

    def Move(self, unit: int, count: int, waitTime: float = OPERATION_WAIT_TIME) -> int:
        """
        Call IUIAutomationTextRange::Move.
        Move the text range forward or backward by the specified number of text units.
        unit: int, a value in class `TextUnit`.
        count: int, the number of text units to move.
               A positive value moves the text range forward.
               A negative value moves the text range backward. Zero has no effect.
        waitTime: float.
        Return: int, the number of text units actually moved.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-move
        """
        ret = self.textRange.Move(unit, count)
        time.sleep(waitTime)
        return ret

    def MoveEndpointByRange(
        self,
        srcEndPoint: int,
        textRange: "TextRange",
        targetEndPoint: int,
        waitTime: float = OPERATION_WAIT_TIME,
    ) -> bool:
        """
        Call IUIAutomationTextRange::MoveEndpointByRange.
        Move one endpoint of the current text range to the specified endpoint of a second text range.
        srcEndPoint: int, a value in class `TextPatternRangeEndpoint`.
        textRange: `TextRange`.
        targetEndPoint: int, a value in class `TextPatternRangeEndpoint`.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-moveendpointbyrange
        """
        ret = (
            self.textRange.MoveEndpointByRange(srcEndPoint, textRange.textRange, targetEndPoint)
            == S_OK
        )
        time.sleep(waitTime)
        return ret

    def MoveEndpointByUnit(
        self,
        endPoint: int,
        unit: int,
        count: int,
        waitTime: float = OPERATION_WAIT_TIME,
    ) -> int:
        """
        Call IUIAutomationTextRange::MoveEndpointByUnit.
        Move one endpoint of the text range the specified number of text units within the document range.
        endPoint: int, a value in class `TextPatternRangeEndpoint`.
        unit: int, a value in class `TextUnit`.
        count: int, the number of units to move.
                    A positive count moves the endpoint forward.
                    A negative count moves backward.
                    A count of 0 has no effect.
        waitTime: float.
        Return int, the count of units actually moved.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-moveendpointbyunit
        """
        ret = self.textRange.MoveEndpointByUnit(endPoint, unit, count)
        time.sleep(waitTime)
        return ret

    def RemoveFromSelection(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationTextRange::RemoveFromSelection.
        Remove the text range from an existing collection of selected text in a text container that supports multiple, disjoint selections.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-removefromselection
        """
        ret = self.textRange.RemoveFromSelection() == S_OK
        time.sleep(waitTime)
        return ret

    def ScrollIntoView(self, alignTop: bool = True, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationTextRange::ScrollIntoView.
        Cause the text control to scroll until the text range is visible in the viewport.
        alignTop: bool, True if the text control should be scrolled so that the text range is flush with the top of the viewport;
                        False if it should be flush with the bottom of the viewport.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-scrollintoview
        """
        ret = self.textRange.ScrollIntoView(int(alignTop)) == S_OK
        time.sleep(waitTime)
        return ret

    def Select(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationTextRange::Select.
        Select the span of text that corresponds to this text range, and remove any previous selection.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-select
        """
        ret = self.textRange.Select() == S_OK
        time.sleep(waitTime)
        return ret

    def IsDegenerate(self) -> bool:
        """
        Return bool, True if this is an empty (degenerate) range such as a caret,
            i.e. its Start and End endpoints are at the same location.
        A convenience built on CompareEndpoints(Start, self, End) == 0.
        """
        return self.CompareEndpoints(TextPatternRangeEndpoint.Start, self, TextPatternRangeEndpoint.End) == 0

    def Collapse(self, toEnd: bool = False, waitTime: float = 0.0) -> bool:
        """
        Collapse the range to a single point (a degenerate range), discarding its span.
        toEnd: bool, False collapses to the Start endpoint, True collapses to the End endpoint.
        waitTime: float, defaults to 0 because this only manipulates the client-side range
            and does not drive any UI.
        Return bool, True if succeed otherwise False.
        A convenience built on MoveEndpointByRange.
        """
        if toEnd:
            return self.MoveEndpointByRange(
                TextPatternRangeEndpoint.Start, self, TextPatternRangeEndpoint.End, waitTime
            )
        return self.MoveEndpointByRange(
            TextPatternRangeEndpoint.End, self, TextPatternRangeEndpoint.Start, waitTime
        )

    def GetStartOffset(self, waitTime: float = 0.0) -> int:
        """
        Return int, the offset of this range's Start endpoint from DocumentRange start,
            in TextUnit.Character steps. Derived by walking a collapsed clone back to the
            document start, so cost can be O(offset) on providers with a linear Move.
        waitTime: float, defaults to 0 because this is a read-only query.
        """
        clone = self.Clone()
        clone.Collapse(toEnd=False, waitTime=waitTime)
        # 0x7FFFFFFF (INT32 max) is larger than any real document, so a single
        # backward Move lands on DocumentRange start; negate the (negative)
        # moved count to get the positive offset.
        moved = clone.Move(TextUnit.Character, -0x7FFFFFFF, waitTime)
        return -moved

    def GetTextBefore(self, count: int, waitTime: float = 0.0) -> str:
        """
        Return str, up to `count` characters immediately before this range's Start endpoint.
        waitTime: float, defaults to 0 because this is a read-only query.
        """
        clone = self.Clone()
        clone.Collapse(toEnd=False, waitTime=waitTime)
        clone.MoveEndpointByUnit(
            TextPatternRangeEndpoint.Start, TextUnit.Character, -count, waitTime
        )
        return clone.GetText()

    def GetTextAfter(self, count: int, waitTime: float = 0.0) -> str:
        """
        Return str, up to `count` characters immediately after this range's End endpoint.
        waitTime: float, defaults to 0 because this is a read-only query.
        """
        clone = self.Clone()
        clone.Collapse(toEnd=True, waitTime=waitTime)
        clone.MoveEndpointByUnit(TextPatternRangeEndpoint.End, TextUnit.Character, count, waitTime)
        return clone.GetText()


class TextChildPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationtextchildpattern"""
        self.pattern = pattern

    @property
    def TextContainer(self) -> "Control":
        """
        Property TextContainer.
        Call IUIAutomationSelectionContainer::get_TextContainer.
        Return `Control` subclass, the nearest ancestor element that supports the Text control pattern.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextchildpattern-get_textcontainer
        """
        return Control.CreateControlFromElement(self.pattern.TextContainer)

    @property
    def TextRange(self) -> TextRange:
        """
        Property TextRange.
        Call IUIAutomationSelectionContainer::get_TextRange.
        Return `TextRange`, a text range that encloses this child element.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextchildpattern-get_textrange
        """
        return TextRange(self.pattern.TextRange)


class TextEditPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationtexteditpattern"""
        self.pattern = pattern

    def GetActiveComposition(self) -> TextRange | None:
        """
        Call IUIAutomationTextEditPattern::GetActiveComposition.
        Return `TextRange` or None, the active composition.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtexteditpattern-getactivecomposition
        """
        textRange = self.pattern.GetActiveComposition()
        if textRange:
            return TextRange(textRange=textRange)
        return None

    def GetConversionTarget(self) -> TextRange | None:
        """
        Call IUIAutomationTextEditPattern::GetConversionTarget.
        Return `TextRange` or None, the current conversion target range..
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtexteditpattern-getconversiontarget
        """
        textRange = self.pattern.GetConversionTarget()
        if textRange:
            return TextRange(textRange=textRange)
        return None


class TextPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationtextpattern"""
        self.pattern = pattern

    @property
    def DocumentRange(self) -> TextRange:
        """
        Property DocumentRange.
        Call IUIAutomationTextPattern::get_DocumentRange.
        Return `TextRange`, a text range that encloses the main text of a document.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextpattern-get_documentrange
        """
        return TextRange(self.pattern.DocumentRange)

    @property
    def SupportedTextSelection(self) -> bool:
        """
        Property SupportedTextSelection.
        Call IUIAutomationTextPattern::get_SupportedTextSelection.
        Return bool, specifies the type of text selection that is supported by the control.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextpattern-get_supportedtextselection
        """
        return bool(self.pattern.SupportedTextSelection)

    def GetSelection(self) -> List[TextRange]:
        """
        Call IUIAutomationTextPattern::GetSelection.
        Return List[TextRange], a list of `TextRange`, represents the currently selected text in a text-based control.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextpattern-getselection
        """
        eleArray = self.pattern.GetSelection()
        if eleArray:
            textRanges = []
            for i in range(eleArray.Length):
                ele = eleArray.GetElement(i)
                textRanges.append(TextRange(textRange=ele))
            return textRanges
        return []

    def GetFirstSelection(self) -> TextRange | None:
        """
        Return `TextRange` or None, the first currently selected range, or None if the
            control has no selection. A convenience over GetSelection()[0].
        """
        selections = self.GetSelection()
        return selections[0] if selections else None

    def GetVisibleRanges(self) -> List[TextRange]:
        """
        Call IUIAutomationTextPattern::GetVisibleRanges.
        Return List[TextRange], a list of `TextRange`, disjoint text ranges from a text-based control
                     where each text range represents a contiguous span of visible text.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextpattern-getvisibleranges
        """
        eleArray = self.pattern.GetVisibleRanges()
        if eleArray:
            textRanges = []
            for i in range(eleArray.Length):
                ele = eleArray.GetElement(i)
                textRanges.append(TextRange(textRange=ele))
            return textRanges
        return []

    def RangeFromChild(self, child) -> TextRange | None:
        """
        Call IUIAutomationTextPattern::RangeFromChild.
        child: `Control` or its subclass.
        Return `TextRange` or None, a text range enclosing a child element such as an image,
            hyperlink, Microsoft Excel spreadsheet, or other embedded object.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextpattern-rangefromchild
        """
        textRange = self.pattern.RangeFromChild(Control.Element)
        if textRange:
            return TextRange(textRange=textRange)
        return None

    def RangeFromPoint(self, x: int, y: int) -> TextRange | None:
        """
        Call IUIAutomationTextPattern::RangeFromPoint.
        child: `Control` or its subclass.
        Return `TextRange` or None, the degenerate (empty) text range nearest to the specified screen coordinates.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextpattern-rangefrompoint
        """
        textRange = self.pattern.RangeFromPoint(ctypes.wintypes.POINT(x, y))
        if textRange:
            return TextRange(textRange=textRange)
        return None


class TextPattern2:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationtextpattern2"""
        self.pattern = pattern


class TogglePattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationtogglepattern"""
        self.pattern = pattern

    @property
    def ToggleState(self) -> int:
        """
        Property ToggleState.
        Call IUIAutomationTogglePattern::get_CurrentToggleState.
        Return int, a value in class `ToggleState`, the state of the control.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtogglepattern-get_currenttogglestate
        """
        return self.pattern.CurrentToggleState

    def Toggle(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationTogglePattern::Toggle.
        Cycle through the toggle states of the control.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtogglepattern-toggle
        """
        ret = self.pattern.Toggle() == S_OK
        time.sleep(waitTime)
        return ret

    def SetToggleState(self, toggleState: int, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        for i in range(6):
            if self.ToggleState == toggleState:
                return True
            self.Toggle(waitTime)
        return False


class TransformPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationtransformpattern"""
        self.pattern = pattern

    @property
    def CanMove(self) -> bool:
        """
        Property CanMove.
        Call IUIAutomationTransformPattern::get_CurrentCanMove.
        Return bool, indicates whether the element can be moved.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtransformpattern-get_currentcanmove
        """
        return bool(self.pattern.CurrentCanMove)

    @property
    def CanResize(self) -> bool:
        """
        Property CanResize.
        Call IUIAutomationTransformPattern::get_CurrentCanResize.
        Return bool, indicates whether the element can be resized.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtransformpattern-get_currentcanresize
        """
        return bool(self.pattern.CurrentCanResize)

    @property
    def CanRotate(self) -> bool:
        """
        Property CanRotate.
        Call IUIAutomationTransformPattern::get_CurrentCanRotate.
        Return bool, indicates whether the element can be rotated.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtransformpattern-get_currentcanrotate
        """
        return bool(self.pattern.CurrentCanRotate)

    def Move(self, x: int, y: int, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationTransformPattern::Move.
        Move the UI Automation element.
        x: int.
        y: int.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtransformpattern-move
        """
        ret = self.pattern.Move(x, y) == S_OK
        time.sleep(waitTime)
        return ret

    def Resize(self, width: int, height: int, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationTransformPattern::Resize.
        Resize the UI Automation element.
        width: int.
        height: int.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtransformpattern-resize
        """
        ret = self.pattern.Resize(width, height) == S_OK
        time.sleep(waitTime)
        return ret

    def Rotate(self, degrees: int, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationTransformPattern::Rotate.
        Rotates the UI Automation element.
        degrees: int.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtransformpattern-rotate
        """
        ret = self.pattern.Rotate(degrees) == S_OK
        time.sleep(waitTime)
        return ret


class TransformPattern2:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationtransformpattern2"""
        self.pattern = pattern

    @property
    def CanZoom(self) -> bool:
        """
        Property CanZoom.
        Call IUIAutomationTransformPattern2::get_CurrentCanZoom.
        Return bool, indicates whether the control supports zooming of its viewport.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtransformpattern2-get_CurrentCanZoom
        """
        return bool(self.pattern.CurrentCanZoom)

    @property
    def ZoomLevel(self) -> float:
        """
        Property ZoomLevel.
        Call IUIAutomationTransformPattern2::get_CurrentZoomLevel.
        Return float, the zoom level of the control's viewport.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtransformpattern2-get_currentzoomlevel
        """
        return self.pattern.CurrentZoomLevel

    @property
    def ZoomMaximum(self) -> float:
        """
        Property ZoomMaximum.
        Call IUIAutomationTransformPattern2::get_CurrentZoomMaximum.
        Return float, the maximum zoom level of the control's viewport.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtransformpattern2-get_currentzoommaximum
        """
        return self.pattern.CurrentZoomMaximum

    @property
    def ZoomMinimum(self) -> float:
        """
        Property ZoomMinimum.
        Call IUIAutomationTransformPattern2::get_CurrentZoomMinimum.
        Return float, the minimum zoom level of the control's viewport.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtransformpattern2-get_currentzoomminimum
        """
        return self.pattern.CurrentZoomMinimum

    def Zoom(self, zoomLevel: float, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationTransformPattern2::Zoom.
        Zoom the viewport of the control.
        zoomLevel: float for int.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtransformpattern2-zoom
        """
        ret = self.pattern.Zoom(zoomLevel) == S_OK
        time.sleep(waitTime)
        return ret

    def ZoomByUnit(self, zoomUnit: int, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationTransformPattern2::ZoomByUnit.
        Zoom the viewport of the control by the specified unit.
        zoomUnit: int, a value in class `ZoomUnit`.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtransformpattern2-zoombyunit
        """
        ret = self.pattern.ZoomByUnit(zoomUnit) == S_OK
        time.sleep(waitTime)
        return ret


class ValuePattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationvaluepattern"""
        self.pattern = pattern

    @property
    def IsReadOnly(self) -> bool:
        """
        Property IsReadOnly.
        Call IUIAutomationTransformPattern2::IUIAutomationValuePattern::get_CurrentIsReadOnly.
        Return bool, indicates whether the value of the element is read-only.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationvaluepattern-get_currentisreadonly
        """
        return bool(self.pattern.CurrentIsReadOnly)

    @property
    def Value(self) -> str:
        """
        Property Value.
        Call IUIAutomationTransformPattern2::IUIAutomationValuePattern::get_CurrentValue.
        Return str, the value of the element.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationvaluepattern-get_currentvalue
        """
        return self.pattern.CurrentValue

    def SetValue(self, value: str, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationTransformPattern2::IUIAutomationValuePattern::SetValue.
        Set the value of the element.
        value: str.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationvaluepattern-setvalue
        """
        ret = self.pattern.SetValue(value) == S_OK
        time.sleep(waitTime)
        return ret


class VirtualizedItemPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationvirtualizeditempattern"""
        self.pattern = pattern

    def Realize(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationVirtualizedItemPattern::Realize.
        Create a full UI Automation element for a virtualized item.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationvirtualizeditempattern-realize
        """
        ret = self.pattern.Realize() == S_OK
        time.sleep(waitTime)
        return ret


class WindowPattern:
    def __init__(self, pattern=None):
        """Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationwindowpattern"""
        self.pattern = pattern

    def Close(self, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationWindowPattern::Close.
        Close the window.
        waitTime: float.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationwindowpattern-close
        """
        ret = self.pattern.Close() == S_OK
        time.sleep(waitTime)
        return ret

    @property
    def CanMaximize(self) -> bool:
        """
        Property CanMaximize.
        Call IUIAutomationWindowPattern::get_CurrentCanMaximize.
        Return bool, indicates whether the window can be maximized.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationwindowpattern-get_currentcanmaximize
        """
        return bool(self.pattern.CurrentCanMaximize)

    @property
    def CanMinimize(self) -> bool:
        """
        Property CanMinimize.
        Call IUIAutomationWindowPattern::get_CurrentCanMinimize.
        Return bool, indicates whether the window can be minimized.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationwindowpattern-get_currentcanminimize
        """
        return bool(self.pattern.CurrentCanMinimize)

    @property
    def IsModal(self) -> bool:
        """
        Property IsModal.
        Call IUIAutomationWindowPattern::get_CurrentIsModal.
        Return bool, indicates whether the window is modal.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationwindowpattern-get_currentismodal
        """
        return bool(self.pattern.CurrentIsModal)

    @property
    def IsTopmost(self) -> bool:
        """
        Property IsTopmost.
        Call IUIAutomationWindowPattern::get_CurrentIsTopmost.
        Return bool, indicates whether the window is the topmost element in the z-order.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationwindowpattern-get_currentistopmost
        """
        return bool(self.pattern.CurrentIsTopmost)

    @property
    def WindowInteractionState(self) -> int:
        """
        Property WindowInteractionState.
        Call IUIAutomationWindowPattern::get_CurrentWindowInteractionState.
        Return int, a value in class `WindowInteractionState`,
                    the current state of the window for the purposes of user interaction.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationwindowpattern-get_currentwindowinteractionstate
        """
        return self.pattern.CurrentWindowInteractionState

    @property
    def WindowVisualState(self) -> int:
        """
        Property WindowVisualState.
        Call IUIAutomationWindowPattern::get_CurrentWindowVisualState.
        Return int, a value in class `WindowVisualState`,
                    the visual state of the window; that is, whether it is in the normal, maximized, or minimized state.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationwindowpattern-get_currentwindowvisualstate
        """
        return self.pattern.CurrentWindowVisualState

    def SetWindowVisualState(self, state: int, waitTime: float = OPERATION_WAIT_TIME) -> bool:
        """
        Call IUIAutomationWindowPattern::SetWindowVisualState.
        Minimize, maximize, or restore the window.
        state: int, a value in class `WindowVisualState`.
        waitTime: float.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationwindowpattern-setwindowvisualstate
        """
        ret = self.pattern.SetWindowVisualState(state) == S_OK
        time.sleep(waitTime)
        return ret

    def WaitForInputIdle(self, milliseconds: int) -> bool:
        """
        Call IUIAutomationWindowPattern::WaitForInputIdle.
        Cause the calling code to block for the specified time or
            until the associated process enters an idle state, whichever completes first.
        milliseconds: int.
        Return bool, True if succeed otherwise False.
        Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationwindowpattern-waitforinputidle
        """
        return self.pattern.WaitForInputIdle(milliseconds) == S_OK


PatternConstructors = {
    PatternId.AnnotationPattern: AnnotationPattern,
    PatternId.CustomNavigationPattern: CustomNavigationPattern,
    PatternId.DockPattern: DockPattern,
    PatternId.DragPattern: DragPattern,
    PatternId.DropTargetPattern: DropTargetPattern,
    PatternId.ExpandCollapsePattern: ExpandCollapsePattern,
    PatternId.GridItemPattern: GridItemPattern,
    PatternId.GridPattern: GridPattern,
    PatternId.InvokePattern: InvokePattern,
    PatternId.ItemContainerPattern: ItemContainerPattern,
    PatternId.LegacyIAccessiblePattern: LegacyIAccessiblePattern,
    PatternId.MultipleViewPattern: MultipleViewPattern,
    PatternId.ObjectModelPattern: ObjectModelPattern,
    PatternId.RangeValuePattern: RangeValuePattern,
    PatternId.ScrollItemPattern: ScrollItemPattern,
    PatternId.ScrollPattern: ScrollPattern,
    PatternId.SelectionItemPattern: SelectionItemPattern,
    PatternId.SelectionPattern: SelectionPattern,
    PatternId.SpreadsheetItemPattern: SpreadsheetItemPattern,
    PatternId.SpreadsheetPattern: SpreadsheetPattern,
    PatternId.StylesPattern: StylesPattern,
    PatternId.SynchronizedInputPattern: SynchronizedInputPattern,
    PatternId.TableItemPattern: TableItemPattern,
    PatternId.TablePattern: TablePattern,
    PatternId.TextChildPattern: TextChildPattern,
    PatternId.TextEditPattern: TextEditPattern,
    PatternId.TextPattern: TextPattern,
    PatternId.TextPattern2: TextPattern2,
    PatternId.TogglePattern: TogglePattern,
    PatternId.TransformPattern: TransformPattern,
    PatternId.TransformPattern2: TransformPattern2,
    PatternId.ValuePattern: ValuePattern,
    PatternId.VirtualizedItemPattern: VirtualizedItemPattern,
    PatternId.WindowPattern: WindowPattern,
}


def CreatePattern(patternId: int, pattern: ctypes.POINTER(comtypes.IUnknown)):
    """Create a concreate pattern by pattern id and pattern(POINTER(IUnknown))."""
    subPattern = pattern.QueryInterface(GetPatternIdInterface(patternId))
    if subPattern:
        return PatternConstructors[patternId](pattern=subPattern)

import comtypes

from .core import _AutomationClient


class TextEditTextChangedEventHandler(comtypes.COMObject):
    """
    Generic `IUIAutomationTextEditTextChangedEventHandler` wrapper: invokes `callback` with
    (sender: Control | None, change_type: int, event_strings: List[str]) whenever a watched
    control's text changes via IME composition or autocorrect/autocomplete.

    Pair with `core.AddTextEditTextChangedEventHandler` / `RemoveTextEditTextChangedEventHandler`:

        handler = TextEditTextChangedEventHandler(my_callback)
        AddTextEditTextChangedEventHandler(
            control.Element, TreeScope.TreeScope_Element,
            TextEditChangeType.TextEditChangeType_AutoCorrect, None, handler,
        )
        ...
        RemoveTextEditTextChangedEventHandler(control.Element, handler)

    Keep a reference to `handler` for as long as it should stay registered -- COM only holds
    a reference while the subscription is active, and letting the last Python reference drop
    can tear it down early.
    """

    _com_interfaces_ = [
        _AutomationClient.instance().UIAutomationCore.IUIAutomationTextEditTextChangedEventHandler
    ]

    def __init__(self, callback):
        self._callback = callback
        super().__init__()

    def HandleTextEditTextChangedEvent(self, sender, changeType, eventStrings):
        try:
            from .controls import Control

            control = Control.CreateControlFromElement(sender) if sender else None
            strings = list(eventStrings) if eventStrings else []
            self._callback(control, changeType, strings)
        except Exception:
            pass
        return 0  # S_OK


class EventId:
    """
    EventId from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/winauto/uiauto-event-ids
    """

    UIA_ToolTipOpenedEventId = 20000
    UIA_ToolTipClosedEventId = 20001
    UIA_StructureChangedEventId = 20002
    UIA_MenuOpenedEventId = 20003
    UIA_AutomationPropertyChangedEventId = 20004
    UIA_AutomationFocusChangedEventId = 20005
    UIA_AsyncContentLoadedEventId = 20006
    UIA_MenuClosedEventId = 20007
    UIA_LayoutInvalidatedEventId = 20008
    UIA_Invoke_InvokedEventId = 20009
    UIA_SelectionItem_ElementAddedToSelectionEventId = 20010
    UIA_SelectionItem_ElementRemovedFromSelectionEventId = 20011
    UIA_SelectionItem_ElementSelectedEventId = 20012
    UIA_Selection_InvalidatedEventId = 20013
    UIA_Text_TextSelectionChangedEventId = 20014
    UIA_Text_TextChangedEventId = 20015
    UIA_Window_WindowOpenedEventId = 20016
    UIA_Window_WindowClosedEventId = 20017
    UIA_MenuModeStartEventId = 20018
    UIA_MenuModeEndEventId = 20019
    UIA_InputReachedTargetEventId = 20020
    UIA_InputReachedOtherElementEventId = 20021
    UIA_InputDiscardedEventId = 20022
    UIA_SystemAlertEventId = 20023
    UIA_LiveRegionChangedEventId = 20024
    UIA_HostedFragmentRootsInvalidatedEventId = 20025
    UIA_Drag_DragStartEventId = 20026
    UIA_Drag_DragCancelEventId = 20027
    UIA_Drag_DragCompleteEventId = 20028
    UIA_DropTarget_DragEnterEventId = 20029
    UIA_DropTarget_DragLeaveEventId = 20030
    UIA_DropTarget_DroppedEventId = 20031
    UIA_TextEdit_TextChangedEventId = 20032
    UIA_TextEdit_ConversionTargetChangedEventId = 20033
    UIA_ChangesEventId = 20034
    UIA_NotificationEventId = 20035
    UIA_ActiveTextPositionChangedEventId = 20036


EventIdNames = {
    EventId.UIA_ToolTipOpenedEventId: "UIA_ToolTipOpenedEventId",
    EventId.UIA_ToolTipClosedEventId: "UIA_ToolTipClosedEventId",
    EventId.UIA_StructureChangedEventId: "UIA_StructureChangedEventId",
    EventId.UIA_MenuOpenedEventId: "UIA_MenuOpenedEventId",
    EventId.UIA_AutomationPropertyChangedEventId: "UIA_AutomationPropertyChangedEventId",
    EventId.UIA_AutomationFocusChangedEventId: "UIA_AutomationFocusChangedEventId",
    EventId.UIA_AsyncContentLoadedEventId: "UIA_AsyncContentLoadedEventId",
    EventId.UIA_MenuClosedEventId: "UIA_MenuClosedEventId",
    EventId.UIA_LayoutInvalidatedEventId: "UIA_LayoutInvalidatedEventId",
    EventId.UIA_Invoke_InvokedEventId: "UIA_Invoke_InvokedEventId",
    EventId.UIA_SelectionItem_ElementAddedToSelectionEventId: "UIA_SelectionItem_ElementAddedToSelectionEventId",
    EventId.UIA_SelectionItem_ElementRemovedFromSelectionEventId: "UIA_SelectionItem_ElementRemovedFromSelectionEventId",
    EventId.UIA_SelectionItem_ElementSelectedEventId: "UIA_SelectionItem_ElementSelectedEventId",
    EventId.UIA_Selection_InvalidatedEventId: "UIA_Selection_InvalidatedEventId",
    EventId.UIA_Text_TextSelectionChangedEventId: "UIA_Text_TextSelectionChangedEventId",
    EventId.UIA_Text_TextChangedEventId: "UIA_Text_TextChangedEventId",
    EventId.UIA_Window_WindowOpenedEventId: "UIA_Window_WindowOpenedEventId",
    EventId.UIA_Window_WindowClosedEventId: "UIA_Window_WindowClosedEventId",
    EventId.UIA_MenuModeStartEventId: "UIA_MenuModeStartEventId",
    EventId.UIA_MenuModeEndEventId: "UIA_MenuModeEndEventId",
    EventId.UIA_InputReachedTargetEventId: "UIA_InputReachedTargetEventId",
    EventId.UIA_InputReachedOtherElementEventId: "UIA_InputReachedOtherElementEventId",
    EventId.UIA_InputDiscardedEventId: "UIA_InputDiscardedEventId",
    EventId.UIA_SystemAlertEventId: "UIA_SystemAlertEventId",
    EventId.UIA_LiveRegionChangedEventId: "UIA_LiveRegionChangedEventId",
    EventId.UIA_HostedFragmentRootsInvalidatedEventId: "UIA_HostedFragmentRootsInvalidatedEventId",
    EventId.UIA_Drag_DragStartEventId: "UIA_Drag_DragStartEventId",
    EventId.UIA_Drag_DragCancelEventId: "UIA_Drag_DragCancelEventId",
    EventId.UIA_Drag_DragCompleteEventId: "UIA_Drag_DragCompleteEventId",
    EventId.UIA_DropTarget_DragEnterEventId: "UIA_DropTarget_DragEnterEventId",
    EventId.UIA_DropTarget_DragLeaveEventId: "UIA_DropTarget_DragLeaveEventId",
    EventId.UIA_DropTarget_DroppedEventId: "UIA_DropTarget_DroppedEventId",
    EventId.UIA_TextEdit_TextChangedEventId: "UIA_TextEdit_TextChangedEventId",
    EventId.UIA_TextEdit_ConversionTargetChangedEventId: "UIA_TextEdit_ConversionTargetChangedEventId",
    EventId.UIA_ChangesEventId: "UIA_ChangesEventId",
    EventId.UIA_NotificationEventId: "UIA_NotificationEventId",
    EventId.UIA_ActiveTextPositionChangedEventId: "UIA_ActiveTextPositionChangedEventId",
}

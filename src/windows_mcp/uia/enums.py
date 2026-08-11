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

import os
import sys
import time
import ctypes
import ctypes.wintypes
from enum import IntEnum, IntFlag
from typing import Any


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


class ControlType:
    """
    ControlType from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/winauto/uiauto-controltype-ids
    """

    AppBarControl = 50040
    ButtonControl = 50000
    CalendarControl = 50001
    CheckBoxControl = 50002
    ComboBoxControl = 50003
    CustomControl = 50025
    DataGridControl = 50028
    DataItemControl = 50029
    DocumentControl = 50030
    EditControl = 50004
    GroupControl = 50026
    HeaderControl = 50034
    HeaderItemControl = 50035
    HyperlinkControl = 50005
    ImageControl = 50006
    ListControl = 50008
    ListItemControl = 50007
    MenuBarControl = 50010
    MenuControl = 50009
    MenuItemControl = 50011
    PaneControl = 50033
    ProgressBarControl = 50012
    RadioButtonControl = 50013
    ScrollBarControl = 50014
    SemanticZoomControl = 50039
    SeparatorControl = 50038
    SliderControl = 50015
    SpinnerControl = 50016
    SplitButtonControl = 50031
    StatusBarControl = 50017
    TabControl = 50018
    TabItemControl = 50019
    TableControl = 50036
    TextControl = 50020
    ThumbControl = 50027
    TitleBarControl = 50037
    ToolBarControl = 50021
    ToolTipControl = 50022
    TreeControl = 50023
    TreeItemControl = 50024
    WindowControl = 50032


ControlTypeNames = {
    ControlType.AppBarControl: "AppBarControl",
    ControlType.ButtonControl: "ButtonControl",
    ControlType.CalendarControl: "CalendarControl",
    ControlType.CheckBoxControl: "CheckBoxControl",
    ControlType.ComboBoxControl: "ComboBoxControl",
    ControlType.CustomControl: "CustomControl",
    ControlType.DataGridControl: "DataGridControl",
    ControlType.DataItemControl: "DataItemControl",
    ControlType.DocumentControl: "DocumentControl",
    ControlType.EditControl: "EditControl",
    ControlType.GroupControl: "GroupControl",
    ControlType.HeaderControl: "HeaderControl",
    ControlType.HeaderItemControl: "HeaderItemControl",
    ControlType.HyperlinkControl: "HyperlinkControl",
    ControlType.ImageControl: "ImageControl",
    ControlType.ListControl: "ListControl",
    ControlType.ListItemControl: "ListItemControl",
    ControlType.MenuBarControl: "MenuBarControl",
    ControlType.MenuControl: "MenuControl",
    ControlType.MenuItemControl: "MenuItemControl",
    ControlType.PaneControl: "PaneControl",
    ControlType.ProgressBarControl: "ProgressBarControl",
    ControlType.RadioButtonControl: "RadioButtonControl",
    ControlType.ScrollBarControl: "ScrollBarControl",
    ControlType.SemanticZoomControl: "SemanticZoomControl",
    ControlType.SeparatorControl: "SeparatorControl",
    ControlType.SliderControl: "SliderControl",
    ControlType.SpinnerControl: "SpinnerControl",
    ControlType.SplitButtonControl: "SplitButtonControl",
    ControlType.StatusBarControl: "StatusBarControl",
    ControlType.TabControl: "TabControl",
    ControlType.TabItemControl: "TabItemControl",
    ControlType.TableControl: "TableControl",
    ControlType.TextControl: "TextControl",
    ControlType.ThumbControl: "ThumbControl",
    ControlType.TitleBarControl: "TitleBarControl",
    ControlType.ToolBarControl: "ToolBarControl",
    ControlType.ToolTipControl: "ToolTipControl",
    ControlType.TreeControl: "TreeControl",
    ControlType.TreeItemControl: "TreeItemControl",
    ControlType.WindowControl: "WindowControl",
}


class PatternId:
    """
    PatternId from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/winauto/uiauto-controlpattern-ids
    """

    AnnotationPattern = 10023
    CustomNavigationPattern = 10033
    DockPattern = 10011
    DragPattern = 10030
    DropTargetPattern = 10031
    ExpandCollapsePattern = 10005
    GridItemPattern = 10007
    GridPattern = 10006
    InvokePattern = 10000
    ItemContainerPattern = 10019
    LegacyIAccessiblePattern = 10018
    MultipleViewPattern = 10008
    ObjectModelPattern = 10022
    RangeValuePattern = 10003
    ScrollItemPattern = 10017
    ScrollPattern = 10004
    SelectionItemPattern = 10010
    SelectionPattern = 10001
    SpreadsheetItemPattern = 10027
    SpreadsheetPattern = 10026
    StylesPattern = 10025
    SynchronizedInputPattern = 10021
    TableItemPattern = 10013
    TablePattern = 10012
    TextChildPattern = 10029
    TextEditPattern = 10032
    TextPattern = 10014
    TextPattern2 = 10024
    TogglePattern = 10015
    TransformPattern = 10016
    TransformPattern2 = 10028
    ValuePattern = 10002
    VirtualizedItemPattern = 10020
    WindowPattern = 10009
    SelectionPattern2 = 10034
    StructuredMarkupPattern = 10035


PatternIdNames = {
    PatternId.AnnotationPattern: "AnnotationPattern",
    PatternId.CustomNavigationPattern: "CustomNavigationPattern",
    PatternId.DockPattern: "DockPattern",
    PatternId.DragPattern: "DragPattern",
    PatternId.DropTargetPattern: "DropTargetPattern",
    PatternId.ExpandCollapsePattern: "ExpandCollapsePattern",
    PatternId.GridItemPattern: "GridItemPattern",
    PatternId.GridPattern: "GridPattern",
    PatternId.InvokePattern: "InvokePattern",
    PatternId.ItemContainerPattern: "ItemContainerPattern",
    PatternId.LegacyIAccessiblePattern: "LegacyIAccessiblePattern",
    PatternId.MultipleViewPattern: "MultipleViewPattern",
    PatternId.ObjectModelPattern: "ObjectModelPattern",
    PatternId.RangeValuePattern: "RangeValuePattern",
    PatternId.ScrollItemPattern: "ScrollItemPattern",
    PatternId.ScrollPattern: "ScrollPattern",
    PatternId.SelectionItemPattern: "SelectionItemPattern",
    PatternId.SelectionPattern: "SelectionPattern",
    PatternId.SpreadsheetItemPattern: "SpreadsheetItemPattern",
    PatternId.SpreadsheetPattern: "SpreadsheetPattern",
    PatternId.StylesPattern: "StylesPattern",
    PatternId.SynchronizedInputPattern: "SynchronizedInputPattern",
    PatternId.TableItemPattern: "TableItemPattern",
    PatternId.TablePattern: "TablePattern",
    PatternId.TextChildPattern: "TextChildPattern",
    PatternId.TextEditPattern: "TextEditPattern",
    PatternId.TextPattern: "TextPattern",
    PatternId.TextPattern2: "TextPattern2",
    PatternId.TogglePattern: "TogglePattern",
    PatternId.TransformPattern: "TransformPattern",
    PatternId.TransformPattern2: "TransformPattern2",
    PatternId.ValuePattern: "ValuePattern",
    PatternId.VirtualizedItemPattern: "VirtualizedItemPattern",
    PatternId.WindowPattern: "WindowPattern",
    PatternId.SelectionPattern2: "SelectionPattern2",
    PatternId.StructuredMarkupPattern: "StructuredMarkupPattern",
}


class PropertyId:
    """
    PropertyId from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/winauto/uiauto-automation-element-propids
    Refer https://docs.microsoft.com/en-us/windows/win32/winauto/uiauto-control-pattern-propids
    """

    AcceleratorKeyProperty = 30006
    AccessKeyProperty = 30007
    AnnotationAnnotationTypeIdProperty = 30113
    AnnotationAnnotationTypeNameProperty = 30114
    AnnotationAuthorProperty = 30115
    AnnotationDateTimeProperty = 30116
    AnnotationObjectsProperty = 30156
    AnnotationTargetProperty = 30117
    AnnotationTypesProperty = 30155
    AriaPropertiesProperty = 30102
    AriaRoleProperty = 30101
    AutomationIdProperty = 30011
    BoundingRectangleProperty = 30001
    CenterPointProperty = 30165
    ClassNameProperty = 30012
    ClickablePointProperty = 30014
    ControlTypeProperty = 30003
    ControllerForProperty = 30104
    CultureProperty = 30015
    DescribedByProperty = 30105
    DockDockPositionProperty = 30069
    DragDropEffectProperty = 30139
    DragDropEffectsProperty = 30140
    DragGrabbedItemsProperty = 30144
    DragIsGrabbedProperty = 30138
    DropTargetDropTargetEffectProperty = 30142
    DropTargetDropTargetEffectsProperty = 30143
    ExpandCollapseExpandCollapseStateProperty = 30070
    FillColorProperty = 30160
    FillTypeProperty = 30162
    FlowsFromProperty = 30148
    FlowsToProperty = 30106
    FrameworkIdProperty = 30024
    FullDescriptionProperty = 30159
    GridColumnCountProperty = 30063
    GridItemColumnProperty = 30065
    GridItemColumnSpanProperty = 30067
    GridItemContainingGridProperty = 30068
    GridItemRowProperty = 30064
    GridItemRowSpanProperty = 30066
    GridRowCountProperty = 30062
    HasKeyboardFocusProperty = 30008
    HeadingLevelProperty = 30173
    HelpTextProperty = 30013
    IsAnnotationPatternAvailableProperty = 30118
    IsContentElementProperty = 30017
    IsControlElementProperty = 30016
    IsCustomNavigationPatternAvailableProperty = 30151
    IsDataValidForFormProperty = 30103
    IsDialogProperty = 30174
    IsDockPatternAvailableProperty = 30027
    IsDragPatternAvailableProperty = 30137
    IsDropTargetPatternAvailableProperty = 30141
    IsEnabledProperty = 30010
    IsExpandCollapsePatternAvailableProperty = 30028
    IsGridItemPatternAvailableProperty = 30029
    IsGridPatternAvailableProperty = 30030
    IsInvokePatternAvailableProperty = 30031
    IsItemContainerPatternAvailableProperty = 30108
    IsKeyboardFocusableProperty = 30009
    IsLegacyIAccessiblePatternAvailableProperty = 30090
    IsMultipleViewPatternAvailableProperty = 30032
    IsObjectModelPatternAvailableProperty = 30112
    IsOffscreenProperty = 30022
    IsPasswordProperty = 30019
    IsPeripheralProperty = 30150
    IsRangeValuePatternAvailableProperty = 30033
    IsRequiredForFormProperty = 30025
    IsScrollItemPatternAvailableProperty = 30035
    IsScrollPatternAvailableProperty = 30034
    IsSelectionItemPatternAvailableProperty = 30036
    IsSelectionPattern2AvailableProperty = 30168
    IsSelectionPatternAvailableProperty = 30037
    IsSpreadsheetItemPatternAvailableProperty = 30132
    IsSpreadsheetPatternAvailableProperty = 30128
    IsStructuredMarkupPatternAvailableProperty = 30175
    IsStylesPatternAvailableProperty = 30127
    IsSynchronizedInputPatternAvailableProperty = 30110
    IsTableItemPatternAvailableProperty = 30039
    IsTablePatternAvailableProperty = 30038
    IsTextChildPatternAvailableProperty = 30136
    IsTextEditPatternAvailableProperty = 30149
    IsTextPattern2AvailableProperty = 30119
    IsTextPatternAvailableProperty = 30040
    IsTogglePatternAvailableProperty = 30041
    IsTransformPattern2AvailableProperty = 30134
    IsTransformPatternAvailableProperty = 30042
    IsValuePatternAvailableProperty = 30043
    IsVirtualizedItemPatternAvailableProperty = 30109
    IsWindowPatternAvailableProperty = 30044
    ItemStatusProperty = 30026
    ItemTypeProperty = 30021
    LabeledByProperty = 30018
    LandmarkTypeProperty = 30157
    LegacyIAccessibleChildIdProperty = 30091
    LegacyIAccessibleDefaultActionProperty = 30100
    LegacyIAccessibleDescriptionProperty = 30094
    LegacyIAccessibleHelpProperty = 30097
    LegacyIAccessibleKeyboardShortcutProperty = 30098
    LegacyIAccessibleNameProperty = 30092
    LegacyIAccessibleRoleProperty = 30095
    LegacyIAccessibleSelectionProperty = 30099
    LegacyIAccessibleStateProperty = 30096
    LegacyIAccessibleValueProperty = 30093
    LevelProperty = 30154
    LiveSettingProperty = 30135
    LocalizedControlTypeProperty = 30004
    LocalizedLandmarkTypeProperty = 30158
    MultipleViewCurrentViewProperty = 30071
    MultipleViewSupportedViewsProperty = 30072
    NameProperty = 30005
    NativeWindowHandleProperty = 30020
    OptimizeForVisualContentProperty = 30111
    OrientationProperty = 30023
    OutlineColorProperty = 30161
    OutlineThicknessProperty = 30164
    PositionInSetProperty = 30152
    ProcessIdProperty = 30002
    ProviderDescriptionProperty = 30107
    RangeValueIsReadOnlyProperty = 30048
    RangeValueLargeChangeProperty = 30051
    RangeValueMaximumProperty = 30050
    RangeValueMinimumProperty = 30049
    RangeValueSmallChangeProperty = 30052
    RangeValueValueProperty = 30047
    RotationProperty = 30166
    RuntimeIdProperty = 30000
    ScrollHorizontalScrollPercentProperty = 30053
    ScrollHorizontalViewSizeProperty = 30054
    ScrollHorizontallyScrollableProperty = 30057
    ScrollVerticalScrollPercentProperty = 30055
    ScrollVerticalViewSizeProperty = 30056
    ScrollVerticallyScrollableProperty = 30058
    Selection2CurrentSelectedItemProperty = 30171
    Selection2FirstSelectedItemProperty = 30169
    Selection2ItemCountProperty = 30172
    Selection2LastSelectedItemProperty = 30170
    SelectionCanSelectMultipleProperty = 30060
    SelectionIsSelectionRequiredProperty = 30061
    SelectionItemIsSelectedProperty = 30079
    SelectionItemSelectionContainerProperty = 30080
    SelectionSelectionProperty = 30059
    SizeOfSetProperty = 30153
    SizeProperty = 30167
    SpreadsheetItemAnnotationObjectsProperty = 30130
    SpreadsheetItemAnnotationTypesProperty = 30131
    SpreadsheetItemFormulaProperty = 30129
    StylesExtendedPropertiesProperty = 30126
    StylesFillColorProperty = 30122
    StylesFillPatternColorProperty = 30125
    StylesFillPatternStyleProperty = 30123
    StylesShapeProperty = 30124
    StylesStyleIdProperty = 30120
    StylesStyleNameProperty = 30121
    TableColumnHeadersProperty = 30082
    TableItemColumnHeaderItemsProperty = 30085
    TableItemRowHeaderItemsProperty = 30084
    TableRowHeadersProperty = 30081
    TableRowOrColumnMajorProperty = 30083
    ToggleToggleStateProperty = 30086
    Transform2CanZoomProperty = 30133
    Transform2ZoomLevelProperty = 30145
    Transform2ZoomMaximumProperty = 30147
    Transform2ZoomMinimumProperty = 30146
    TransformCanMoveProperty = 30087
    TransformCanResizeProperty = 30088
    TransformCanRotateProperty = 30089
    ValueIsReadOnlyProperty = 30046
    ValueValueProperty = 30045
    VisualEffectsProperty = 30163
    WindowCanMaximizeProperty = 30073
    WindowCanMinimizeProperty = 30074
    WindowIsModalProperty = 30077
    WindowIsTopmostProperty = 30078
    WindowWindowInteractionStateProperty = 30076
    WindowWindowVisualStateProperty = 30075


PropertyIdNames = {
    PropertyId.AcceleratorKeyProperty: "AcceleratorKeyProperty",
    PropertyId.AccessKeyProperty: "AccessKeyProperty",
    PropertyId.AnnotationAnnotationTypeIdProperty: "AnnotationAnnotationTypeIdProperty",
    PropertyId.AnnotationAnnotationTypeNameProperty: "AnnotationAnnotationTypeNameProperty",
    PropertyId.AnnotationAuthorProperty: "AnnotationAuthorProperty",
    PropertyId.AnnotationDateTimeProperty: "AnnotationDateTimeProperty",
    PropertyId.AnnotationObjectsProperty: "AnnotationObjectsProperty",
    PropertyId.AnnotationTargetProperty: "AnnotationTargetProperty",
    PropertyId.AnnotationTypesProperty: "AnnotationTypesProperty",
    PropertyId.AriaPropertiesProperty: "AriaPropertiesProperty",
    PropertyId.AriaRoleProperty: "AriaRoleProperty",
    PropertyId.AutomationIdProperty: "AutomationIdProperty",
    PropertyId.BoundingRectangleProperty: "BoundingRectangleProperty",
    PropertyId.CenterPointProperty: "CenterPointProperty",
    PropertyId.ClassNameProperty: "ClassNameProperty",
    PropertyId.ClickablePointProperty: "ClickablePointProperty",
    PropertyId.ControlTypeProperty: "ControlTypeProperty",
    PropertyId.ControllerForProperty: "ControllerForProperty",
    PropertyId.CultureProperty: "CultureProperty",
    PropertyId.DescribedByProperty: "DescribedByProperty",
    PropertyId.DockDockPositionProperty: "DockDockPositionProperty",
    PropertyId.DragDropEffectProperty: "DragDropEffectProperty",
    PropertyId.DragDropEffectsProperty: "DragDropEffectsProperty",
    PropertyId.DragGrabbedItemsProperty: "DragGrabbedItemsProperty",
    PropertyId.DragIsGrabbedProperty: "DragIsGrabbedProperty",
    PropertyId.DropTargetDropTargetEffectProperty: "DropTargetDropTargetEffectProperty",
    PropertyId.DropTargetDropTargetEffectsProperty: "DropTargetDropTargetEffectsProperty",
    PropertyId.ExpandCollapseExpandCollapseStateProperty: "ExpandCollapseExpandCollapseStateProperty",
    PropertyId.FillColorProperty: "FillColorProperty",
    PropertyId.FillTypeProperty: "FillTypeProperty",
    PropertyId.FlowsFromProperty: "FlowsFromProperty",
    PropertyId.FlowsToProperty: "FlowsToProperty",
    PropertyId.FrameworkIdProperty: "FrameworkIdProperty",
    PropertyId.FullDescriptionProperty: "FullDescriptionProperty",
    PropertyId.GridColumnCountProperty: "GridColumnCountProperty",
    PropertyId.GridItemColumnProperty: "GridItemColumnProperty",
    PropertyId.GridItemColumnSpanProperty: "GridItemColumnSpanProperty",
    PropertyId.GridItemContainingGridProperty: "GridItemContainingGridProperty",
    PropertyId.GridItemRowProperty: "GridItemRowProperty",
    PropertyId.GridItemRowSpanProperty: "GridItemRowSpanProperty",
    PropertyId.GridRowCountProperty: "GridRowCountProperty",
    PropertyId.HasKeyboardFocusProperty: "HasKeyboardFocusProperty",
    PropertyId.HeadingLevelProperty: "HeadingLevelProperty",
    PropertyId.HelpTextProperty: "HelpTextProperty",
    PropertyId.IsAnnotationPatternAvailableProperty: "IsAnnotationPatternAvailableProperty",
    PropertyId.IsContentElementProperty: "IsContentElementProperty",
    PropertyId.IsControlElementProperty: "IsControlElementProperty",
    PropertyId.IsCustomNavigationPatternAvailableProperty: "IsCustomNavigationPatternAvailableProperty",
    PropertyId.IsDataValidForFormProperty: "IsDataValidForFormProperty",
    PropertyId.IsDialogProperty: "IsDialogProperty",
    PropertyId.IsDockPatternAvailableProperty: "IsDockPatternAvailableProperty",
    PropertyId.IsDragPatternAvailableProperty: "IsDragPatternAvailableProperty",
    PropertyId.IsDropTargetPatternAvailableProperty: "IsDropTargetPatternAvailableProperty",
    PropertyId.IsEnabledProperty: "IsEnabledProperty",
    PropertyId.IsExpandCollapsePatternAvailableProperty: "IsExpandCollapsePatternAvailableProperty",
    PropertyId.IsGridItemPatternAvailableProperty: "IsGridItemPatternAvailableProperty",
    PropertyId.IsGridPatternAvailableProperty: "IsGridPatternAvailableProperty",
    PropertyId.IsInvokePatternAvailableProperty: "IsInvokePatternAvailableProperty",
    PropertyId.IsItemContainerPatternAvailableProperty: "IsItemContainerPatternAvailableProperty",
    PropertyId.IsKeyboardFocusableProperty: "IsKeyboardFocusableProperty",
    PropertyId.IsLegacyIAccessiblePatternAvailableProperty: "IsLegacyIAccessiblePatternAvailableProperty",
    PropertyId.IsMultipleViewPatternAvailableProperty: "IsMultipleViewPatternAvailableProperty",
    PropertyId.IsObjectModelPatternAvailableProperty: "IsObjectModelPatternAvailableProperty",
    PropertyId.IsOffscreenProperty: "IsOffscreenProperty",
    PropertyId.IsPasswordProperty: "IsPasswordProperty",
    PropertyId.IsPeripheralProperty: "IsPeripheralProperty",
    PropertyId.IsRangeValuePatternAvailableProperty: "IsRangeValuePatternAvailableProperty",
    PropertyId.IsRequiredForFormProperty: "IsRequiredForFormProperty",
    PropertyId.IsScrollItemPatternAvailableProperty: "IsScrollItemPatternAvailableProperty",
    PropertyId.IsScrollPatternAvailableProperty: "IsScrollPatternAvailableProperty",
    PropertyId.IsSelectionItemPatternAvailableProperty: "IsSelectionItemPatternAvailableProperty",
    PropertyId.IsSelectionPattern2AvailableProperty: "IsSelectionPattern2AvailableProperty",
    PropertyId.IsSelectionPatternAvailableProperty: "IsSelectionPatternAvailableProperty",
    PropertyId.IsSpreadsheetItemPatternAvailableProperty: "IsSpreadsheetItemPatternAvailableProperty",
    PropertyId.IsSpreadsheetPatternAvailableProperty: "IsSpreadsheetPatternAvailableProperty",
    PropertyId.IsStructuredMarkupPatternAvailableProperty: "IsStructuredMarkupPatternAvailableProperty",
    PropertyId.IsStylesPatternAvailableProperty: "IsStylesPatternAvailableProperty",
    PropertyId.IsSynchronizedInputPatternAvailableProperty: "IsSynchronizedInputPatternAvailableProperty",
    PropertyId.IsTableItemPatternAvailableProperty: "IsTableItemPatternAvailableProperty",
    PropertyId.IsTablePatternAvailableProperty: "IsTablePatternAvailableProperty",
    PropertyId.IsTextChildPatternAvailableProperty: "IsTextChildPatternAvailableProperty",
    PropertyId.IsTextEditPatternAvailableProperty: "IsTextEditPatternAvailableProperty",
    PropertyId.IsTextPattern2AvailableProperty: "IsTextPattern2AvailableProperty",
    PropertyId.IsTextPatternAvailableProperty: "IsTextPatternAvailableProperty",
    PropertyId.IsTogglePatternAvailableProperty: "IsTogglePatternAvailableProperty",
    PropertyId.IsTransformPattern2AvailableProperty: "IsTransformPattern2AvailableProperty",
    PropertyId.IsTransformPatternAvailableProperty: "IsTransformPatternAvailableProperty",
    PropertyId.IsValuePatternAvailableProperty: "IsValuePatternAvailableProperty",
    PropertyId.IsVirtualizedItemPatternAvailableProperty: "IsVirtualizedItemPatternAvailableProperty",
    PropertyId.IsWindowPatternAvailableProperty: "IsWindowPatternAvailableProperty",
    PropertyId.ItemStatusProperty: "ItemStatusProperty",
    PropertyId.ItemTypeProperty: "ItemTypeProperty",
    PropertyId.LabeledByProperty: "LabeledByProperty",
    PropertyId.LandmarkTypeProperty: "LandmarkTypeProperty",
    PropertyId.LegacyIAccessibleChildIdProperty: "LegacyIAccessibleChildIdProperty",
    PropertyId.LegacyIAccessibleDefaultActionProperty: "LegacyIAccessibleDefaultActionProperty",
    PropertyId.LegacyIAccessibleDescriptionProperty: "LegacyIAccessibleDescriptionProperty",
    PropertyId.LegacyIAccessibleHelpProperty: "LegacyIAccessibleHelpProperty",
    PropertyId.LegacyIAccessibleKeyboardShortcutProperty: "LegacyIAccessibleKeyboardShortcutProperty",
    PropertyId.LegacyIAccessibleNameProperty: "LegacyIAccessibleNameProperty",
    PropertyId.LegacyIAccessibleRoleProperty: "LegacyIAccessibleRoleProperty",
    PropertyId.LegacyIAccessibleSelectionProperty: "LegacyIAccessibleSelectionProperty",
    PropertyId.LegacyIAccessibleStateProperty: "LegacyIAccessibleStateProperty",
    PropertyId.LegacyIAccessibleValueProperty: "LegacyIAccessibleValueProperty",
    PropertyId.LevelProperty: "LevelProperty",
    PropertyId.LiveSettingProperty: "LiveSettingProperty",
    PropertyId.LocalizedControlTypeProperty: "LocalizedControlTypeProperty",
    PropertyId.LocalizedLandmarkTypeProperty: "LocalizedLandmarkTypeProperty",
    PropertyId.MultipleViewCurrentViewProperty: "MultipleViewCurrentViewProperty",
    PropertyId.MultipleViewSupportedViewsProperty: "MultipleViewSupportedViewsProperty",
    PropertyId.NameProperty: "NameProperty",
    PropertyId.NativeWindowHandleProperty: "NativeWindowHandleProperty",
    PropertyId.OptimizeForVisualContentProperty: "OptimizeForVisualContentProperty",
    PropertyId.OrientationProperty: "OrientationProperty",
    PropertyId.OutlineColorProperty: "OutlineColorProperty",
    PropertyId.OutlineThicknessProperty: "OutlineThicknessProperty",
    PropertyId.PositionInSetProperty: "PositionInSetProperty",
    PropertyId.ProcessIdProperty: "ProcessIdProperty",
    PropertyId.ProviderDescriptionProperty: "ProviderDescriptionProperty",
    PropertyId.RangeValueIsReadOnlyProperty: "RangeValueIsReadOnlyProperty",
    PropertyId.RangeValueLargeChangeProperty: "RangeValueLargeChangeProperty",
    PropertyId.RangeValueMaximumProperty: "RangeValueMaximumProperty",
    PropertyId.RangeValueMinimumProperty: "RangeValueMinimumProperty",
    PropertyId.RangeValueSmallChangeProperty: "RangeValueSmallChangeProperty",
    PropertyId.RangeValueValueProperty: "RangeValueValueProperty",
    PropertyId.RotationProperty: "RotationProperty",
    PropertyId.RuntimeIdProperty: "RuntimeIdProperty",
    PropertyId.ScrollHorizontalScrollPercentProperty: "ScrollHorizontalScrollPercentProperty",
    PropertyId.ScrollHorizontalViewSizeProperty: "ScrollHorizontalViewSizeProperty",
    PropertyId.ScrollHorizontallyScrollableProperty: "ScrollHorizontallyScrollableProperty",
    PropertyId.ScrollVerticalScrollPercentProperty: "ScrollVerticalScrollPercentProperty",
    PropertyId.ScrollVerticalViewSizeProperty: "ScrollVerticalViewSizeProperty",
    PropertyId.ScrollVerticallyScrollableProperty: "ScrollVerticallyScrollableProperty",
    PropertyId.Selection2CurrentSelectedItemProperty: "Selection2CurrentSelectedItemProperty",
    PropertyId.Selection2FirstSelectedItemProperty: "Selection2FirstSelectedItemProperty",
    PropertyId.Selection2ItemCountProperty: "Selection2ItemCountProperty",
    PropertyId.Selection2LastSelectedItemProperty: "Selection2LastSelectedItemProperty",
    PropertyId.SelectionCanSelectMultipleProperty: "SelectionCanSelectMultipleProperty",
    PropertyId.SelectionIsSelectionRequiredProperty: "SelectionIsSelectionRequiredProperty",
    PropertyId.SelectionItemIsSelectedProperty: "SelectionItemIsSelectedProperty",
    PropertyId.SelectionItemSelectionContainerProperty: "SelectionItemSelectionContainerProperty",
    PropertyId.SelectionSelectionProperty: "SelectionSelectionProperty",
    PropertyId.SizeOfSetProperty: "SizeOfSetProperty",
    PropertyId.SizeProperty: "SizeProperty",
    PropertyId.SpreadsheetItemAnnotationObjectsProperty: "SpreadsheetItemAnnotationObjectsProperty",
    PropertyId.SpreadsheetItemAnnotationTypesProperty: "SpreadsheetItemAnnotationTypesProperty",
    PropertyId.SpreadsheetItemFormulaProperty: "SpreadsheetItemFormulaProperty",
    PropertyId.StylesExtendedPropertiesProperty: "StylesExtendedPropertiesProperty",
    PropertyId.StylesFillColorProperty: "StylesFillColorProperty",
    PropertyId.StylesFillPatternColorProperty: "StylesFillPatternColorProperty",
    PropertyId.StylesFillPatternStyleProperty: "StylesFillPatternStyleProperty",
    PropertyId.StylesShapeProperty: "StylesShapeProperty",
    PropertyId.StylesStyleIdProperty: "StylesStyleIdProperty",
    PropertyId.StylesStyleNameProperty: "StylesStyleNameProperty",
    PropertyId.TableColumnHeadersProperty: "TableColumnHeadersProperty",
    PropertyId.TableItemColumnHeaderItemsProperty: "TableItemColumnHeaderItemsProperty",
    PropertyId.TableItemRowHeaderItemsProperty: "TableItemRowHeaderItemsProperty",
    PropertyId.TableRowHeadersProperty: "TableRowHeadersProperty",
    PropertyId.TableRowOrColumnMajorProperty: "TableRowOrColumnMajorProperty",
    PropertyId.ToggleToggleStateProperty: "ToggleToggleStateProperty",
    PropertyId.Transform2CanZoomProperty: "Transform2CanZoomProperty",
    PropertyId.Transform2ZoomLevelProperty: "Transform2ZoomLevelProperty",
    PropertyId.Transform2ZoomMaximumProperty: "Transform2ZoomMaximumProperty",
    PropertyId.Transform2ZoomMinimumProperty: "Transform2ZoomMinimumProperty",
    PropertyId.TransformCanMoveProperty: "TransformCanMoveProperty",
    PropertyId.TransformCanResizeProperty: "TransformCanResizeProperty",
    PropertyId.TransformCanRotateProperty: "TransformCanRotateProperty",
    PropertyId.ValueIsReadOnlyProperty: "ValueIsReadOnlyProperty",
    PropertyId.ValueValueProperty: "ValueValueProperty",
    PropertyId.VisualEffectsProperty: "VisualEffectsProperty",
    PropertyId.WindowCanMaximizeProperty: "WindowCanMaximizeProperty",
    PropertyId.WindowCanMinimizeProperty: "WindowCanMinimizeProperty",
    PropertyId.WindowIsModalProperty: "WindowIsModalProperty",
    PropertyId.WindowIsTopmostProperty: "WindowIsTopmostProperty",
    PropertyId.WindowWindowInteractionStateProperty: "WindowWindowInteractionStateProperty",
    PropertyId.WindowWindowVisualStateProperty: "WindowWindowVisualStateProperty",
}


class AccessibleRole(IntEnum):
    """
    AccessibleRole from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/dotnet/api/system.windows.forms.accessiblerole?view=netframework-4.8
    """

    TitleBar = 0x1
    MenuBar = 0x2
    ScrollBar = 0x3
    Grip = 0x4
    Sound = 0x5
    Cursor = 0x6
    Caret = 0x7
    Alert = 0x8
    Window = 0x9
    Client = 0xA
    MenuPopup = 0xB
    MenuItem = 0xC
    ToolTip = 0xD
    Application = 0xE
    Document = 0xF
    Pane = 0x10
    Chart = 0x11
    Dialog = 0x12
    Border = 0x13
    Grouping = 0x14
    Separator = 0x15
    Toolbar = 0x16
    StatusBar = 0x17
    Table = 0x18
    ColumnHeader = 0x19
    RowHeader = 0x1A
    Column = 0x1B
    Row = 0x1C
    Cell = 0x1D
    Link = 0x1E
    HelpBalloon = 0x1F
    Character = 0x20
    List = 0x21
    ListItem = 0x22
    Outline = 0x23
    OutlineItem = 0x24
    PageTab = 0x25
    PropertyPage = 0x26
    Indicator = 0x27
    Graphic = 0x28
    StaticText = 0x29
    Text = 0x2A
    PushButton = 0x2B
    CheckButton = 0x2C
    RadioButton = 0x2D
    ComboBox = 0x2E
    DropList = 0x2F
    ProgressBar = 0x30
    Dial = 0x31
    HotkeyField = 0x32
    Slider = 0x33
    SpinButton = 0x34
    Diagram = 0x35
    Animation = 0x36
    Equation = 0x37
    ButtonDropDown = 0x38
    ButtonMenu = 0x39
    ButtonDropDownGrid = 0x3A
    WhiteSpace = 0x3B
    PageTabList = 0x3C
    Clock = 0x3D
    SplitButton = 0x3E
    IpAddress = 0x3F
    OutlineButton = 0x40


AccessibleRoleNames = {v: k for k, v in AccessibleRole.__dict__.items() if not k.startswith("_")}


class AccessibleState(IntFlag):
    """
    AccessibleState from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/dotnet/api/system.windows.forms.accessiblestates?view=netframework-4.8
    """

    Normal = 0
    Unavailable = 0x1
    Selected = 0x2
    Focused = 0x4
    Pressed = 0x8
    Checked = 0x10
    Mixed = 0x20
    Indeterminate = 0x20
    ReadOnly = 0x40
    HotTracked = 0x80
    Default = 0x100
    Expanded = 0x200
    Collapsed = 0x400
    Busy = 0x800
    Floating = 0x1000
    Marqueed = 0x2000
    Animated = 0x4000
    Invisible = 0x8000
    Offscreen = 0x10000
    Sizeable = 0x20000
    Moveable = 0x40000
    SelfVoicing = 0x80000
    Focusable = 0x100000
    Selectable = 0x200000
    Linked = 0x400000
    Traversed = 0x800000
    MultiSelectable = 0x1000000
    ExtSelectable = 0x2000000
    AlertLow = 0x4000000
    AlertMedium = 0x8000000
    AlertHigh = 0x10000000
    Protected = 0x20000000
    Valid = 0x7FFFFFFF
    HasPopup = 0x40000000


class AccessibleSelection:
    """
    AccessibleSelection from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/dotnet/api/system.windows.forms.accessibleselection?view=netframework-4.8
    """

    None_ = 0
    TakeFocus = 0x1
    TakeSelection = 0x2
    ExtendSelection = 0x4
    AddSelection = 0x8
    RemoveSelection = 0x10


class AnnotationType(IntEnum):
    """
    AnnotationType from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/winauto/uiauto-annotation-type-identifiers
    """

    AdvancedProofingIssue = 60020
    Author = 60019
    CircularReferenceError = 60022
    Comment = 60003
    ConflictingChange = 60018
    DataValidationError = 60021
    DeletionChange = 60012
    EditingLockedChange = 60016
    Endnote = 60009
    ExternalChange = 60017
    Footer = 60007
    Footnote = 60010
    FormatChange = 60014
    FormulaError = 60004
    GrammarError = 60002
    Header = 60006
    Highlighted = 60008
    InsertionChange = 60011
    Mathematics = 60023
    MoveChange = 60013
    SpellingError = 60001
    TrackChanges = 60005
    Unknown = 60000
    UnsyncedChange = 60015


class NavigateDirection(IntEnum):
    """
    NavigateDirection from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationcore/ne-uiautomationcore-navigatedirection
    """

    Parent = 0
    NextSibling = 1
    PreviousSibling = 2
    FirstChild = 3
    LastChild = 4


class DockPosition(IntEnum):
    """
    DockPosition from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationcore/ne-uiautomationcore-dockposition
    """

    Top = 0
    Left = 1
    Bottom = 2
    Right = 3
    Fill = 4
    None_ = 5


class ScrollAmount(IntEnum):
    """
    ScrollAmount from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationcore/ne-uiautomationcore-scrollamount
    """

    LargeDecrement = 0
    SmallDecrement = 1
    NoAmount = 2
    LargeIncrement = 3
    SmallIncrement = 4


class StyleId(IntEnum):
    """
    StyleId from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/winauto/uiauto-style-identifiers
    """

    Custom = 70000
    Heading1 = 70001
    Heading2 = 70002
    Heading3 = 70003
    Heading4 = 70004
    Heading5 = 70005
    Heading6 = 70006
    Heading7 = 70007
    Heading8 = 70008
    Heading9 = 70009
    Title = 70010
    Subtitle = 70011
    Normal = 70012
    Emphasis = 70013
    Quote = 70014
    BulletedList = 70015
    NumberedList = 70016


class RowOrColumnMajor(IntEnum):
    """
    RowOrColumnMajor from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationcore/ne-uiautomationcore-roworcolumnmajor
    """

    RowMajor = 0
    ColumnMajor = 1
    Indeterminate = 2


class HeadingLevel(IntEnum):
    """
    HeadingLevel from IUIAutomation, the value of `PropertyId.HeadingLevelProperty`.

    The identifiers start at 80050 rather than 0, so a raw property value is
    meaningless without this mapping: `None_` (80050) means "not a heading" and is what
    the overwhelming majority of elements report.
    Refer https://docs.microsoft.com/en-us/windows/win32/winauto/uiauto-headinglevel-ids
    """

    None_ = 80050
    Level1 = 80051
    Level2 = 80052
    Level3 = 80053
    Level4 = 80054
    Level5 = 80055
    Level6 = 80056
    Level7 = 80057
    Level8 = 80058
    Level9 = 80059


class LandmarkType(IntEnum):
    """
    LandmarkType from IUIAutomation, the value of `PropertyId.LandmarkTypeProperty`.

    Marks a region's semantic role in a page or window (ARIA landmarks, and the WinUI /
    Win32 equivalents). Like `HeadingLevel` the identifiers start at 80000, but unlike it
    the "not a landmark" case is reported as plain `0`, not as a member of the 80000
    range -- hence `None_ = 0`.

    Observed in the wild on Paint, File Explorer, Snipping Tool, Calculator and Electron
    apps; `Form` is the only member not seen during that sweep.
    Refer https://docs.microsoft.com/en-us/windows/win32/winauto/uiauto-landmarktype-ids
    """

    None_ = 0
    Custom = 80000
    Form = 80001
    Main = 80002
    Navigation = 80003
    Search = 80004


class ExpandCollapseState(IntEnum):
    """
    ExpandCollapseState from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationcore/ne-uiautomationcore-expandcollapsestate
    """

    Collapsed = 0
    Expanded = 1
    PartiallyExpanded = 2
    LeafNode = 3


class OrientationType(IntEnum):
    """
    OrientationType from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationcore/ne-uiautomationcore-orientationtype
    """

    None_ = 0
    Horizontal = 1
    Vertical = 2


class ToggleState(IntEnum):
    """
    ToggleState from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationcore/ne-uiautomationcore-togglestate
    """

    Off = 0
    On = 1
    Indeterminate = 2


class TextPatternRangeEndpoint(IntEnum):
    """
    TextPatternRangeEndpoint from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationcore/ne-uiautomationcore-textpatternrangeendpoint
    """

    Start = 0
    End = 1


class TextAttributeId:
    """
    TextAttributeId from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/winauto/uiauto-textattribute-ids
    """

    AfterParagraphSpacingAttribute = 40042
    AnimationStyleAttribute = 40000
    AnnotationObjectsAttribute = 40032
    AnnotationTypesAttribute = 40031
    BackgroundColorAttribute = 40001
    BeforeParagraphSpacingAttribute = 40041
    BulletStyleAttribute = 40002
    CapStyleAttribute = 40003
    CaretBidiModeAttribute = 40039
    CaretPositionAttribute = 40038
    CultureAttribute = 40004
    FontNameAttribute = 40005
    FontSizeAttribute = 40006
    FontWeightAttribute = 40007
    ForegroundColorAttribute = 40008
    HorizontalTextAlignmentAttribute = 40009
    IndentationFirstLineAttribute = 40010
    IndentationLeadingAttribute = 40011
    IndentationTrailingAttribute = 40012
    IsActiveAttribute = 40036
    IsHiddenAttribute = 40013
    IsItalicAttribute = 40014
    IsReadOnlyAttribute = 40015
    IsSubscriptAttribute = 40016
    IsSuperscriptAttribute = 40017
    LineSpacingAttribute = 40040
    LinkAttribute = 40035
    MarginBottomAttribute = 40018
    MarginLeadingAttribute = 40019
    MarginTopAttribute = 40020
    MarginTrailingAttribute = 40021
    OutlineStylesAttribute = 40022
    OverlineColorAttribute = 40023
    OverlineStyleAttribute = 40024
    SayAsInterpretAsAttribute = 40043
    SelectionActiveEndAttribute = 40037
    StrikethroughColorAttribute = 40025
    StrikethroughStyleAttribute = 40026
    StyleIdAttribute = 40034
    StyleNameAttribute = 40033
    TabsAttribute = 40027
    TextFlowDirectionsAttribute = 40028
    UnderlineColorAttribute = 40029
    UnderlineStyleAttribute = 40030


class TextUnit(IntEnum):
    """
    TextUnit from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationcore/ne-uiautomationcore-textunit
    """

    Character = 0
    Format = 1
    Word = 2
    Line = 3
    Paragraph = 4
    Page = 5
    Document = 6


class ZoomUnit(IntEnum):
    """
    ZoomUnit from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationcore/ne-uiautomationcore-zoomunit
    """

    NoAmount = 0
    LargeDecrement = 1
    SmallDecrement = 2
    LargeIncrement = 3
    SmallIncrement = 4


class WindowInteractionState(IntEnum):
    """
    WindowInteractionState from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationcore/ne-uiautomationcore-windowinteractionstate
    """

    Running = 0
    Closing = 1
    ReadyForUserInteraction = 2
    BlockedByModalWindow = 3
    NotResponding = 4


class WindowVisualState(IntEnum):
    """
    WindowVisualState from IUIAutomation.
    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationcore/ne-uiautomationcore-windowvisualstate
    """

    Normal = 0
    Maximized = 1
    Minimized = 2


class ConsoleColor(IntEnum):
    """ConsoleColor from Win32."""

    Default = -1
    Black = 0
    DarkBlue = 1
    DarkGreen = 2
    DarkCyan = 3
    DarkRed = 4
    DarkMagenta = 5
    DarkYellow = 6
    Gray = 7
    DarkGray = 8
    Blue = 9
    Green = 10
    Cyan = 11
    Red = 12
    Magenta = 13
    Yellow = 14
    White = 15


class GAFlag(IntEnum):
    """GAFlag from Win32."""

    Parent = 1
    Root = 2
    RootOwner = 3


class MouseEventFlag(IntFlag):
    """MouseEventFlag from Win32."""

    Move = 0x0001
    LeftDown = 0x0002
    LeftUp = 0x0004
    RightDown = 0x0008
    RightUp = 0x0010
    MiddleDown = 0x0020
    MiddleUp = 0x0040
    XDown = 0x0080
    XUp = 0x0100
    Wheel = 0x0800
    HWheel = 0x1000
    MoveNoCoalesce = 0x2000
    VirtualDesk = 0x4000
    Absolute = 0x8000


class KeyboardEventFlag(IntEnum):
    """KeyboardEventFlag from Win32."""

    KeyDown = 0x0000
    ExtendedKey = 0x0001
    KeyUp = 0x0002
    KeyUnicode = 0x0004
    KeyScanCode = 0x0008


class InputType(IntEnum):
    """InputType from Win32"""

    Mouse = 0
    Keyboard = 1
    Hardware = 2


class ModifierKey(IntFlag):
    """ModifierKey from Win32."""

    Alt = 0x0001
    Control = 0x0002
    Shift = 0x0004
    Win = 0x0008
    NoRepeat = 0x4000


class SW(IntEnum):
    """ShowWindow params from Win32."""

    Hide = 0
    ShowNormal = 1
    Normal = 1
    ShowMinimized = 2
    ShowMaximized = 3
    Maximize = 3
    ShowNoActivate = 4
    Show = 5
    Minimize = 6
    ShowMinNoActive = 7
    ShowNA = 8
    Restore = 9
    ShowDefault = 10
    ForceMinimize = 11
    Max = 11


class SWP(IntFlag):
    """SetWindowPos params from Win32."""

    HWND_Top = 0
    HWND_Bottom = 1
    HWND_Topmost = -1
    HWND_NoTopmost = -2
    SWP_NoSize = 0x0001
    SWP_NoMove = 0x0002
    SWP_NoZOrder = 0x0004
    SWP_NoRedraw = 0x0008
    SWP_NoActivate = 0x0010
    SWP_FrameChanged = 0x0020  # The frame changed: send WM_NCCALCSIZE
    SWP_ShowWindow = 0x0040
    SWP_HideWindow = 0x0080
    SWP_NoCopyBits = 0x0100
    SWP_NoOwnerZOrder = 0x0200  # Don't do owner Z ordering
    SWP_NoSendChanging = 0x0400  # Don't send WM_WINDOWPOSCHANGING
    SWP_DrawFrame = SWP_FrameChanged
    SWP_NoReposition = SWP_NoOwnerZOrder
    SWP_DeferErase = 0x2000
    SWP_AsyncWindowPos = 0x4000


class MB(IntFlag):
    """MessageBox flags from Win32."""

    Ok = 0x00000000
    OkCancel = 0x00000001
    AbortRetryIgnore = 0x00000002
    YesNoCancel = 0x00000003
    YesNo = 0x00000004
    RetryCancel = 0x00000005
    CancelTryContinue = 0x00000006
    IconHand = 0x00000010
    IconQuestion = 0x00000020
    IconExclamation = 0x00000030
    IconAsterisk = 0x00000040
    UserIcon = 0x00000080
    IconWarning = 0x00000030
    IconError = 0x00000010
    IconInformation = 0x00000040
    IconStop = 0x00000010
    DefButton1 = 0x00000000
    DefButton2 = 0x00000100
    DefButton3 = 0x00000200
    DefButton4 = 0x00000300
    ApplModal = 0x00000000
    SystemModal = 0x00001000
    TaskModal = 0x00002000
    Help = 0x00004000  # help button
    NoFocus = 0x00008000
    SetForeground = 0x00010000
    DefaultDesktopOnly = 0x00020000
    Topmost = 0x00040000
    Right = 0x00080000
    RtlReading = 0x00100000
    ServiceNotification = 0x00200000
    ServiceNotificationNT3X = 0x00040000

    TypeMask = 0x0000000F
    IconMask = 0x000000F0
    DefMask = 0x00000F00
    ModeMask = 0x00003000
    MiscMask = 0x0000C000

    IdOk = 1
    IdCancel = 2
    IdAbort = 3
    IdRetry = 4
    IdIgnore = 5
    IdYes = 6
    IdNo = 7
    IdClose = 8
    IdHelp = 9
    IdTryAgain = 10
    IdContinue = 11
    IdTimeout = 32000


class GWL(IntEnum):
    ExStyle = -20
    HInstance = -6
    HwndParent = -8
    ID = -12
    Style = -16
    UserData = -21
    WndProc = -4


class ProcessDpiAwareness(IntEnum):
    DpiUnaware = 0
    SystemDpiAware = 1
    PerMonitorDpiAware = 2


class DpiAwarenessContext(IntEnum):
    Unaware = -1
    SystemAware = -2
    PerMonitorAware = -3
    PerMonitorAwareV2 = -4
    UnawareGdiScaled = -5


class Keys(IntFlag):
    """Key codes from Win32."""

    VK_LBUTTON = 0x01  # Left mouse button
    VK_RBUTTON = 0x02  # Right mouse button
    VK_CANCEL = 0x03  # Control-break processing
    VK_MBUTTON = 0x04  # Middle mouse button (three-button mouse)
    VK_XBUTTON1 = 0x05  # X1 mouse button
    VK_XBUTTON2 = 0x06  # X2 mouse button
    VK_BACK = 0x08  # BACKSPACE key
    VK_TAB = 0x09  # TAB key
    VK_CLEAR = 0x0C  # CLEAR key
    VK_RETURN = 0x0D  # ENTER key
    VK_ENTER = 0x0D
    VK_SHIFT = 0x10  # SHIFT key
    VK_CONTROL = 0x11  # CTRL key
    VK_MENU = 0x12  # ALT key
    VK_PAUSE = 0x13  # PAUSE key
    VK_CAPITAL = 0x14  # CAPS LOCK key
    VK_KANA = 0x15  # IME Kana mode
    VK_HANGUEL = 0x15  # IME Hanguel mode (maintained for compatibility; use VK_HANGUL)
    VK_HANGUL = 0x15  # IME Hangul mode
    VK_JUNJA = 0x17  # IME Junja mode
    VK_FINAL = 0x18  # IME final mode
    VK_HANJA = 0x19  # IME Hanja mode
    VK_KANJI = 0x19  # IME Kanji mode
    VK_ESCAPE = 0x1B  # ESC key
    VK_CONVERT = 0x1C  # IME convert
    VK_NONCONVERT = 0x1D  # IME nonconvert
    VK_ACCEPT = 0x1E  # IME accept
    VK_MODECHANGE = 0x1F  # IME mode change request
    VK_SPACE = 0x20  # SPACEBAR
    VK_PRIOR = 0x21  # PAGE UP key
    VK_PAGEUP = 0x21
    VK_NEXT = 0x22  # PAGE DOWN key
    VK_PAGEDOWN = 0x22
    VK_END = 0x23  # END key
    VK_HOME = 0x24  # HOME key
    VK_LEFT = 0x25  # LEFT ARROW key
    VK_UP = 0x26  # UP ARROW key
    VK_RIGHT = 0x27  # RIGHT ARROW key
    VK_DOWN = 0x28  # DOWN ARROW key
    VK_SELECT = 0x29  # SELECT key
    VK_PRINT = 0x2A  # PRINT key
    VK_EXECUTE = 0x2B  # EXECUTE key
    VK_SNAPSHOT = 0x2C  # PRINT SCREEN key
    VK_INSERT = 0x2D  # INS key
    VK_DELETE = 0x2E  # DEL key
    VK_HELP = 0x2F  # HELP key
    VK_0 = 0x30  # 0 key
    VK_1 = 0x31  # 1 key
    VK_2 = 0x32  # 2 key
    VK_3 = 0x33  # 3 key
    VK_4 = 0x34  # 4 key
    VK_5 = 0x35  # 5 key
    VK_6 = 0x36  # 6 key
    VK_7 = 0x37  # 7 key
    VK_8 = 0x38  # 8 key
    VK_9 = 0x39  # 9 key
    VK_A = 0x41  # A key
    VK_B = 0x42  # B key
    VK_C = 0x43  # C key
    VK_D = 0x44  # D key
    VK_E = 0x45  # E key
    VK_F = 0x46  # F key
    VK_G = 0x47  # G key
    VK_H = 0x48  # H key
    VK_I = 0x49  # I key
    VK_J = 0x4A  # J key
    VK_K = 0x4B  # K key
    VK_L = 0x4C  # L key
    VK_M = 0x4D  # M key
    VK_N = 0x4E  # N key
    VK_O = 0x4F  # O key
    VK_P = 0x50  # P key
    VK_Q = 0x51  # Q key
    VK_R = 0x52  # R key
    VK_S = 0x53  # S key
    VK_T = 0x54  # T key
    VK_U = 0x55  # U key
    VK_V = 0x56  # V key
    VK_W = 0x57  # W key
    VK_X = 0x58  # X key
    VK_Y = 0x59  # Y key
    VK_Z = 0x5A  # Z key
    VK_LWIN = 0x5B  # Left Windows key (Natural keyboard)
    VK_RWIN = 0x5C  # Right Windows key (Natural keyboard)
    VK_APPS = 0x5D  # Applications key (Natural keyboard)
    VK_SLEEP = 0x5F  # Computer Sleep key
    VK_NUMPAD0 = 0x60  # Numeric keypad 0 key
    VK_NUMPAD1 = 0x61  # Numeric keypad 1 key
    VK_NUMPAD2 = 0x62  # Numeric keypad 2 key
    VK_NUMPAD3 = 0x63  # Numeric keypad 3 key
    VK_NUMPAD4 = 0x64  # Numeric keypad 4 key
    VK_NUMPAD5 = 0x65  # Numeric keypad 5 key
    VK_NUMPAD6 = 0x66  # Numeric keypad 6 key
    VK_NUMPAD7 = 0x67  # Numeric keypad 7 key
    VK_NUMPAD8 = 0x68  # Numeric keypad 8 key
    VK_NUMPAD9 = 0x69  # Numeric keypad 9 key
    VK_MULTIPLY = 0x6A  # Multiply key
    VK_ADD = 0x6B  # Add key
    VK_SEPARATOR = 0x6C  # Separator key
    VK_SUBTRACT = 0x6D  # Subtract key
    VK_DECIMAL = 0x6E  # Decimal key
    VK_DIVIDE = 0x6F  # Divide key
    VK_F1 = 0x70  # F1 key
    VK_F2 = 0x71  # F2 key
    VK_F3 = 0x72  # F3 key
    VK_F4 = 0x73  # F4 key
    VK_F5 = 0x74  # F5 key
    VK_F6 = 0x75  # F6 key
    VK_F7 = 0x76  # F7 key
    VK_F8 = 0x77  # F8 key
    VK_F9 = 0x78  # F9 key
    VK_F10 = 0x79  # F10 key
    VK_F11 = 0x7A  # F11 key
    VK_F12 = 0x7B  # F12 key
    VK_F13 = 0x7C  # F13 key
    VK_F14 = 0x7D  # F14 key
    VK_F15 = 0x7E  # F15 key
    VK_F16 = 0x7F  # F16 key
    VK_F17 = 0x80  # F17 key
    VK_F18 = 0x81  # F18 key
    VK_F19 = 0x82  # F19 key
    VK_F20 = 0x83  # F20 key
    VK_F21 = 0x84  # F21 key
    VK_F22 = 0x85  # F22 key
    VK_F23 = 0x86  # F23 key
    VK_F24 = 0x87  # F24 key
    VK_NUMLOCK = 0x90  # NUM LOCK key
    VK_SCROLL = 0x91  # SCROLL LOCK key
    VK_LSHIFT = 0xA0  # Left SHIFT key
    VK_RSHIFT = 0xA1  # Right SHIFT key
    VK_LCONTROL = 0xA2  # Left CONTROL key
    VK_RCONTROL = 0xA3  # Right CONTROL key
    VK_LMENU = 0xA4  # Left MENU key
    VK_RMENU = 0xA5  # Right MENU key
    VK_BROWSER_BACK = 0xA6  # Browser Back key
    VK_BROWSER_FORWARD = 0xA7  # Browser Forward key
    VK_BROWSER_REFRESH = 0xA8  # Browser Refresh key
    VK_BROWSER_STOP = 0xA9  # Browser Stop key
    VK_BROWSER_SEARCH = 0xAA  # Browser Search key
    VK_BROWSER_FAVORITES = 0xAB  # Browser Favorites key
    VK_BROWSER_HOME = 0xAC  # Browser Start and Home key
    VK_VOLUME_MUTE = 0xAD  # Volume Mute key
    VK_VOLUME_DOWN = 0xAE  # Volume Down key
    VK_VOLUME_UP = 0xAF  # Volume Up key
    VK_MEDIA_NEXT_TRACK = 0xB0  # Next Track key
    VK_MEDIA_PREV_TRACK = 0xB1  # Previous Track key
    VK_MEDIA_STOP = 0xB2  # Stop Media key
    VK_MEDIA_PLAY_PAUSE = 0xB3  # Play/Pause Media key
    VK_LAUNCH_MAIL = 0xB4  # Start Mail key
    VK_LAUNCH_MEDIA_SELECT = 0xB5  # Select Media key
    VK_LAUNCH_APP1 = 0xB6  # Start Application 1 key
    VK_LAUNCH_APP2 = 0xB7  # Start Application 2 key
    VK_OEM_1 = 0xBA  # Used for miscellaneous characters; it can vary by keyboard.For the US standard keyboard, the ';:' key
    VK_OEM_PLUS = 0xBB  # For any country/region, the '+' key
    VK_OEM_COMMA = 0xBC  # For any country/region, the ',' key
    VK_OEM_MINUS = 0xBD  # For any country/region, the '-' key
    VK_OEM_PERIOD = 0xBE  # For any country/region, the '.' key
    VK_OEM_2 = 0xBF  # Used for miscellaneous characters; it can vary by keyboard.For the US standard keyboard, the '/?' key
    VK_OEM_3 = 0xC0  # Used for miscellaneous characters; it can vary by keyboard.For the US standard keyboard, the '`~' key
    VK_OEM_4 = 0xDB  # Used for miscellaneous characters; it can vary by keyboard.For the US standard keyboard, the '[{' key
    VK_OEM_5 = 0xDC  # Used for miscellaneous characters; it can vary by keyboard.For the US standard keyboard, the '\|' key
    VK_OEM_6 = 0xDD  # Used for miscellaneous characters; it can vary by keyboard.For the US standard keyboard, the ']}' key
    VK_OEM_7 = 0xDE  # Used for miscellaneous characters; it can vary by keyboard.For the US standard keyboard, the 'single-quote/double-quote' key
    VK_OEM_8 = 0xDF  # Used for miscellaneous characters; it can vary by keyboard.
    VK_OEM_102 = (
        0xE2  # Either the angle bracket key or the backslash key on the RT 102-key keyboard
    )
    VK_PROCESSKEY = 0xE5  # IME PROCESS key
    VK_PACKET = 0xE7  # Used to pass Unicode characters as if they were keystrokes. The VK_PACKET key is the low word of a 32-bit Virtual Key value used for non-keyboard input methods. For more information, see Remark in KEYBDINPUT, SendInput, WM_KEYDOWN, and WM_KeyUp
    VK_ATTN = 0xF6  # Attn key
    VK_CRSEL = 0xF7  # CrSel key
    VK_EXSEL = 0xF8  # ExSel key
    VK_EREOF = 0xF9  # Erase EOF key
    VK_PLAY = 0xFA  # Play key
    VK_ZOOM = 0xFB  # Zoom key
    VK_NONAME = 0xFC  # Reserved
    VK_PA1 = 0xFD  # PA1 key
    VK_OEM_CLEAR = 0xFE  # Clear key


SpecialKeyNames = {
    "LBUTTON": Keys.VK_LBUTTON,  # Left mouse button
    "RBUTTON": Keys.VK_RBUTTON,  # Right mouse button
    "CANCEL": Keys.VK_CANCEL,  # Control-break processing
    "MBUTTON": Keys.VK_MBUTTON,  # Middle mouse button (three-button mouse)
    "XBUTTON1": Keys.VK_XBUTTON1,  # X1 mouse button
    "XBUTTON2": Keys.VK_XBUTTON2,  # X2 mouse button
    "BACK": Keys.VK_BACK,  # BACKSPACE key
    "TAB": Keys.VK_TAB,  # TAB key
    "CLEAR": Keys.VK_CLEAR,  # CLEAR key
    "RETURN": Keys.VK_RETURN,  # ENTER key
    "ENTER": Keys.VK_RETURN,  # ENTER key
    "SHIFT": Keys.VK_SHIFT,  # SHIFT key
    "CTRL": Keys.VK_CONTROL,  # CTRL key
    "CONTROL": Keys.VK_CONTROL,  # CTRL key
    "ALT": Keys.VK_MENU,  # ALT key
    "PAUSE": Keys.VK_PAUSE,  # PAUSE key
    "CAPITAL": Keys.VK_CAPITAL,  # CAPS LOCK key
    "KANA": Keys.VK_KANA,  # IME Kana mode
    "HANGUEL": Keys.VK_HANGUEL,  # IME Hanguel mode (maintained for compatibility; use VK_HANGUL)
    "HANGUL": Keys.VK_HANGUL,  # IME Hangul mode
    "JUNJA": Keys.VK_JUNJA,  # IME Junja mode
    "FINAL": Keys.VK_FINAL,  # IME final mode
    "HANJA": Keys.VK_HANJA,  # IME Hanja mode
    "KANJI": Keys.VK_KANJI,  # IME Kanji mode
    "ESC": Keys.VK_ESCAPE,  # ESC key
    "ESCAPE": Keys.VK_ESCAPE,  # ESC key
    "CONVERT": Keys.VK_CONVERT,  # IME convert
    "NONCONVERT": Keys.VK_NONCONVERT,  # IME nonconvert
    "ACCEPT": Keys.VK_ACCEPT,  # IME accept
    "MODECHANGE": Keys.VK_MODECHANGE,  # IME mode change request
    "SPACE": Keys.VK_SPACE,  # SPACEBAR
    "PRIOR": Keys.VK_PRIOR,  # PAGE UP key
    "PAGEUP": Keys.VK_PRIOR,  # PAGE UP key
    "NEXT": Keys.VK_NEXT,  # PAGE DOWN key
    "PAGEDOWN": Keys.VK_NEXT,  # PAGE DOWN key
    "END": Keys.VK_END,  # END key
    "HOME": Keys.VK_HOME,  # HOME key
    "LEFT": Keys.VK_LEFT,  # LEFT ARROW key
    "UP": Keys.VK_UP,  # UP ARROW key
    "RIGHT": Keys.VK_RIGHT,  # RIGHT ARROW key
    "DOWN": Keys.VK_DOWN,  # DOWN ARROW key
    "SELECT": Keys.VK_SELECT,  # SELECT key
    "PRINT": Keys.VK_PRINT,  # PRINT key
    "EXECUTE": Keys.VK_EXECUTE,  # EXECUTE key
    "SNAPSHOT": Keys.VK_SNAPSHOT,  # PRINT SCREEN key
    "PRINTSCREEN": Keys.VK_SNAPSHOT,  # PRINT SCREEN key
    "INSERT": Keys.VK_INSERT,  # INS key
    "INS": Keys.VK_INSERT,  # INS key
    "DELETE": Keys.VK_DELETE,  # DEL key
    "DEL": Keys.VK_DELETE,  # DEL key
    "HELP": Keys.VK_HELP,  # HELP key
    "WIN": Keys.VK_LWIN,  # Left Windows key (Natural keyboard)
    "LWIN": Keys.VK_LWIN,  # Left Windows key (Natural keyboard)
    "RWIN": Keys.VK_RWIN,  # Right Windows key (Natural keyboard)
    "APPS": Keys.VK_APPS,  # Applications key (Natural keyboard)
    "SLEEP": Keys.VK_SLEEP,  # Computer Sleep key
    "NUMPAD0": Keys.VK_NUMPAD0,  # Numeric keypad 0 key
    "NUMPAD1": Keys.VK_NUMPAD1,  # Numeric keypad 1 key
    "NUMPAD2": Keys.VK_NUMPAD2,  # Numeric keypad 2 key
    "NUMPAD3": Keys.VK_NUMPAD3,  # Numeric keypad 3 key
    "NUMPAD4": Keys.VK_NUMPAD4,  # Numeric keypad 4 key
    "NUMPAD5": Keys.VK_NUMPAD5,  # Numeric keypad 5 key
    "NUMPAD6": Keys.VK_NUMPAD6,  # Numeric keypad 6 key
    "NUMPAD7": Keys.VK_NUMPAD7,  # Numeric keypad 7 key
    "NUMPAD8": Keys.VK_NUMPAD8,  # Numeric keypad 8 key
    "NUMPAD9": Keys.VK_NUMPAD9,  # Numeric keypad 9 key
    "MULTIPLY": Keys.VK_MULTIPLY,  # Multiply key
    "ADD": Keys.VK_ADD,  # Add key
    "SEPARATOR": Keys.VK_SEPARATOR,  # Separator key
    "SUBTRACT": Keys.VK_SUBTRACT,  # Subtract key
    "DECIMAL": Keys.VK_DECIMAL,  # Decimal key
    "DIVIDE": Keys.VK_DIVIDE,  # Divide key
    "F1": Keys.VK_F1,  # F1 key
    "F2": Keys.VK_F2,  # F2 key
    "F3": Keys.VK_F3,  # F3 key
    "F4": Keys.VK_F4,  # F4 key
    "F5": Keys.VK_F5,  # F5 key
    "F6": Keys.VK_F6,  # F6 key
    "F7": Keys.VK_F7,  # F7 key
    "F8": Keys.VK_F8,  # F8 key
    "F9": Keys.VK_F9,  # F9 key
    "F10": Keys.VK_F10,  # F10 key
    "F11": Keys.VK_F11,  # F11 key
    "F12": Keys.VK_F12,  # F12 key
    "F13": Keys.VK_F13,  # F13 key
    "F14": Keys.VK_F14,  # F14 key
    "F15": Keys.VK_F15,  # F15 key
    "F16": Keys.VK_F16,  # F16 key
    "F17": Keys.VK_F17,  # F17 key
    "F18": Keys.VK_F18,  # F18 key
    "F19": Keys.VK_F19,  # F19 key
    "F20": Keys.VK_F20,  # F20 key
    "F21": Keys.VK_F21,  # F21 key
    "F22": Keys.VK_F22,  # F22 key
    "F23": Keys.VK_F23,  # F23 key
    "F24": Keys.VK_F24,  # F24 key
    "NUMLOCK": Keys.VK_NUMLOCK,  # NUM LOCK key
    "SCROLL": Keys.VK_SCROLL,  # SCROLL LOCK key
    "LSHIFT": Keys.VK_LSHIFT,  # Left SHIFT key
    "RSHIFT": Keys.VK_RSHIFT,  # Right SHIFT key
    "LCONTROL": Keys.VK_LCONTROL,  # Left CONTROL key
    "LCTRL": Keys.VK_LCONTROL,  # Left CONTROL key
    "RCONTROL": Keys.VK_RCONTROL,  # Right CONTROL key
    "RCTRL": Keys.VK_RCONTROL,  # Right CONTROL key
    "LALT": Keys.VK_LMENU,  # Left MENU key
    "RALT": Keys.VK_RMENU,  # Right MENU key
    "BROWSER_BACK": Keys.VK_BROWSER_BACK,  # Browser Back key
    "BROWSER_FORWARD": Keys.VK_BROWSER_FORWARD,  # Browser Forward key
    "BROWSER_REFRESH": Keys.VK_BROWSER_REFRESH,  # Browser Refresh key
    "BROWSER_STOP": Keys.VK_BROWSER_STOP,  # Browser Stop key
    "BROWSER_SEARCH": Keys.VK_BROWSER_SEARCH,  # Browser Search key
    "BROWSER_FAVORITES": Keys.VK_BROWSER_FAVORITES,  # Browser Favorites key
    "BROWSER_HOME": Keys.VK_BROWSER_HOME,  # Browser Start and Home key
    "VOLUME_MUTE": Keys.VK_VOLUME_MUTE,  # Volume Mute key
    "VOLUME_DOWN": Keys.VK_VOLUME_DOWN,  # Volume Down key
    "VOLUME_UP": Keys.VK_VOLUME_UP,  # Volume Up key
    "MEDIA_NEXT_TRACK": Keys.VK_MEDIA_NEXT_TRACK,  # Next Track key
    "MEDIA_PREV_TRACK": Keys.VK_MEDIA_PREV_TRACK,  # Previous Track key
    "MEDIA_STOP": Keys.VK_MEDIA_STOP,  # Stop Media key
    "MEDIA_PLAY_PAUSE": Keys.VK_MEDIA_PLAY_PAUSE,  # Play/Pause Media key
    "LAUNCH_MAIL": Keys.VK_LAUNCH_MAIL,  # Start Mail key
    "LAUNCH_MEDIA_SELECT": Keys.VK_LAUNCH_MEDIA_SELECT,  # Select Media key
    "LAUNCH_APP1": Keys.VK_LAUNCH_APP1,  # Start Application 1 key
    "LAUNCH_APP2": Keys.VK_LAUNCH_APP2,  # Start Application 2 key
    "OEM_1": Keys.VK_OEM_1,  # Used for miscellaneous characters; it can vary by keyboard.For the US standard keyboard, the ';:' key
    "OEM_PLUS": Keys.VK_OEM_PLUS,  # For any country/region, the '+' key
    "OEM_COMMA": Keys.VK_OEM_COMMA,  # For any country/region, the ',' key
    "OEM_MINUS": Keys.VK_OEM_MINUS,  # For any country/region, the '-' key
    "OEM_PERIOD": Keys.VK_OEM_PERIOD,  # For any country/region, the '.' key
    "OEM_2": Keys.VK_OEM_2,  # Used for miscellaneous characters; it can vary by keyboard.For the US standard keyboard, the '/?' key
    "OEM_3": Keys.VK_OEM_3,  # Used for miscellaneous characters; it can vary by keyboard.For the US standard keyboard, the '`~' key
    "OEM_4": Keys.VK_OEM_4,  # Used for miscellaneous characters; it can vary by keyboard.For the US standard keyboard, the '[{' key
    "OEM_5": Keys.VK_OEM_5,  # Used for miscellaneous characters; it can vary by keyboard.For the US standard keyboard, the '\|' key
    "OEM_6": Keys.VK_OEM_6,  # Used for miscellaneous characters; it can vary by keyboard.For the US standard keyboard, the ']}' key
    "OEM_7": Keys.VK_OEM_7,  # Used for miscellaneous characters; it can vary by keyboard.For the US standard keyboard, the 'single-quote/double-quote' key
    "OEM_8": Keys.VK_OEM_8,  # Used for miscellaneous characters; it can vary by keyboard.
    "OEM_102": Keys.VK_OEM_102,  # Either the angle bracket key or the backslash key on the RT 102-key keyboard
    "PROCESSKEY": Keys.VK_PROCESSKEY,  # IME PROCESS key
    "PACKET": Keys.VK_PACKET,  # Used to pass Unicode characters as if they were keystrokes. The VK_PACKET key is the low word of a 32-bit Virtual Key value used for non-keyboard input methods. For more information, see Remark in KEYBDINPUT, SendInput, WM_KEYDOWN, and WM_KeyUp
    "ATTN": Keys.VK_ATTN,  # Attn key
    "CRSEL": Keys.VK_CRSEL,  # CrSel key
    "EXSEL": Keys.VK_EXSEL,  # ExSel key
    "EREOF": Keys.VK_EREOF,  # Erase EOF key
    "PLAY": Keys.VK_PLAY,  # Play key
    "ZOOM": Keys.VK_ZOOM,  # Zoom key
    "NONAME": Keys.VK_NONAME,  # Reserved
    "PA1": Keys.VK_PA1,  # PA1 key
    "OEM_CLEAR": Keys.VK_OEM_CLEAR,  # Clear key
}


CharacterCodes = {
    "0": Keys.VK_0,  # 0 key
    "1": Keys.VK_1,  # 1 key
    "2": Keys.VK_2,  # 2 key
    "3": Keys.VK_3,  # 3 key
    "4": Keys.VK_4,  # 4 key
    "5": Keys.VK_5,  # 5 key
    "6": Keys.VK_6,  # 6 key
    "7": Keys.VK_7,  # 7 key
    "8": Keys.VK_8,  # 8 key
    "9": Keys.VK_9,  # 9 key
    "a": Keys.VK_A,  # A key
    "A": Keys.VK_A,  # A key
    "b": Keys.VK_B,  # B key
    "B": Keys.VK_B,  # B key
    "c": Keys.VK_C,  # C key
    "C": Keys.VK_C,  # C key
    "d": Keys.VK_D,  # D key
    "D": Keys.VK_D,  # D key
    "e": Keys.VK_E,  # E key
    "E": Keys.VK_E,  # E key
    "f": Keys.VK_F,  # F key
    "F": Keys.VK_F,  # F key
    "g": Keys.VK_G,  # G key
    "G": Keys.VK_G,  # G key
    "h": Keys.VK_H,  # H key
    "H": Keys.VK_H,  # H key
    "i": Keys.VK_I,  # I key
    "I": Keys.VK_I,  # I key
    "j": Keys.VK_J,  # J key
    "J": Keys.VK_J,  # J key
    "k": Keys.VK_K,  # K key
    "K": Keys.VK_K,  # K key
    "l": Keys.VK_L,  # L key
    "L": Keys.VK_L,  # L key
    "m": Keys.VK_M,  # M key
    "M": Keys.VK_M,  # M key
    "n": Keys.VK_N,  # N key
    "N": Keys.VK_N,  # N key
    "o": Keys.VK_O,  # O key
    "O": Keys.VK_O,  # O key
    "p": Keys.VK_P,  # P key
    "P": Keys.VK_P,  # P key
    "q": Keys.VK_Q,  # Q key
    "Q": Keys.VK_Q,  # Q key
    "r": Keys.VK_R,  # R key
    "R": Keys.VK_R,  # R key
    "s": Keys.VK_S,  # S key
    "S": Keys.VK_S,  # S key
    "t": Keys.VK_T,  # T key
    "T": Keys.VK_T,  # T key
    "u": Keys.VK_U,  # U key
    "U": Keys.VK_U,  # U key
    "v": Keys.VK_V,  # V key
    "V": Keys.VK_V,  # V key
    "w": Keys.VK_W,  # W key
    "W": Keys.VK_W,  # W key
    "x": Keys.VK_X,  # X key
    "X": Keys.VK_X,  # X key
    "y": Keys.VK_Y,  # Y key
    "Y": Keys.VK_Y,  # Y key
    "z": Keys.VK_Z,  # Z key
    "Z": Keys.VK_Z,  # Z key
    " ": Keys.VK_SPACE,  # Space key
    "`": Keys.VK_OEM_3,  # ` key
    #'~' : Keys.VK_OEM_3,                         #~ key
    "-": Keys.VK_OEM_MINUS,  # - key
    #'_' : Keys.VK_OEM_MINUS,                     #_ key
    "=": Keys.VK_OEM_PLUS,  # = key
    #'+' : Keys.VK_OEM_PLUS,                      #+ key
    "[": Keys.VK_OEM_4,  # [ key
    #'{' : Keys.VK_OEM_4,                         #{ key
    "]": Keys.VK_OEM_6,  # ] key
    #'}' : Keys.VK_OEM_6,                         #} key
    "\\": Keys.VK_OEM_5,  # \ key
    #'|' : Keys.VK_OEM_5,                         #| key
    ";": Keys.VK_OEM_1,  # ; key
    #':' : Keys.VK_OEM_1,                         #: key
    "'": Keys.VK_OEM_7,  #' key
    #'"' : Keys.VK_OEM_7,                         #" key
    ",": Keys.VK_OEM_COMMA,  # , key
    #'<' : Keys.VK_OEM_COMMA,                     #< key
    ".": Keys.VK_OEM_PERIOD,  # . key
    #'>' : Keys.VK_OEM_PERIOD,                    #> key
    "/": Keys.VK_OEM_2,  # / key
    #'?' : Keys.VK_OEM_2,                         #? key
}


class ConsoleScreenBufferInfo(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.wintypes._COORD),
        ("dwCursorPosition", ctypes.wintypes._COORD),
        ("wAttributes", ctypes.c_uint),
        ("srWindow", ctypes.wintypes.SMALL_RECT),
        ("dwMaximumWindowSize", ctypes.wintypes._COORD),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", ctypes.wintypes.LONG),
        ("dy", ctypes.wintypes.LONG),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.wintypes.PULONG),
    )


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.wintypes.PULONG),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", ctypes.wintypes.DWORD),
        ("wParamL", ctypes.wintypes.WORD),
        ("wParamH", ctypes.wintypes.WORD),
    )


class _INPUTUnion(ctypes.Union):
    _fields_ = (("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT))


class INPUT(ctypes.Structure):
    _fields_ = (("type", ctypes.wintypes.DWORD), ("union", _INPUTUnion))


class Rect:
    """
    class Rect, like `ctypes.wintypes.RECT`.
    """

    def __init__(self, left: int = 0, top: int = 0, right: int = 0, bottom: int = 0):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    def width(self) -> int:
        return self.right - self.left

    def height(self) -> int:
        return self.bottom - self.top

    def xcenter(self) -> int:
        return self.left + self.width() // 2

    def ycenter(self) -> int:
        return self.top + self.height() // 2

    def isempty(self) -> int:
        return self.width() == 0 or self.height() == 0

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def intersect(self, rect: "Rect") -> "Rect":
        left, top, right, bottom = (
            max(self.left, rect.left),
            max(self.top, rect.top),
            min(self.right, rect.right),
            min(self.bottom, rect.bottom),
        )
        return Rect(left, top, right, bottom)

    def offset(self, x: int, y: int) -> None:
        self.left += x
        self.right += x
        self.top += y
        self.bottom += y

    def __eq__(self, rect):
        return (
            self.left == rect.left
            and self.top == rect.top
            and self.right == rect.right
            and self.bottom == rect.bottom
        )

    def __str__(self) -> str:
        return "({},{},{},{})[{}x{}]".format(
            self.left, self.top, self.right, self.bottom, self.width(), self.height()
        )

    def __repr__(self) -> str:
        return "{}({},{},{},{})[{}x{}]".format(
            self.__class__.__name__,
            self.left,
            self.top,
            self.right,
            self.bottom,
            self.width(),
            self.height(),
        )


class ClipboardFormat(IntEnum):
    CF_TEXT = 1
    CF_BITMAP = 2
    CF_METAFILEPICT = 3
    CF_SYLK = 4
    CF_DIF = 5
    CF_TIFF = 6
    CF_OEMTEXT = 7
    CF_DIB = 8
    CF_PALETTE = 9
    CF_PENDATA = 10
    CF_RIFF = 11
    CF_WAVE = 12
    CF_UNICODETEXT = 13
    CF_ENHMETAFILE = 14
    CF_HDROP = 15
    CF_LOCALE = 16
    CF_DIBV5 = 17
    CF_MAX = 18
    CF_HTML = ctypes.windll.user32.RegisterClipboardFormatW("HTML Format")


class ActiveEnd(IntEnum):
    ActiveEnd_None = 0
    ActiveEnd_Start = 1
    ActiveEnd_End = 2


class AnimationStyle(IntEnum):
    AnimationStyle_None = 0
    AnimationStyle_LasVegasLights = 1
    AnimationStyle_BlinkingBackground = 2
    AnimationStyle_SparkleText = 3
    AnimationStyle_MarchingBlackAnts = 4
    AnimationStyle_MarchingRedAnts = 5
    AnimationStyle_Shimmer = 6
    AnimationStyle_Other = -1


class AsyncContentLoadedState(IntEnum):
    AsyncContentLoadedState_Beginning = 0
    AsyncContentLoadedState_Progress = 1
    AsyncContentLoadedState_Completed = 2


class AutomationElementMode(IntEnum):
    AutomationElementMode_None = 0
    AutomationElementMode_Full = 1


class AutomationIdentifierType(IntEnum):
    AutomationIdentifierType_Property = 0
    AutomationIdentifierType_Pattern = 1
    AutomationIdentifierType_Event = 2
    AutomationIdentifierType_ControlType = 3
    AutomationIdentifierType_TextAttribute = 4
    AutomationIdentifierType_LandmarkType = 5
    AutomationIdentifierType_Annotation = 6
    AutomationIdentifierType_Changes = 7
    AutomationIdentifierType_Style = 8


class BulletStyle(IntEnum):
    BulletStyle_None = 0
    BulletStyle_HollowRoundBullet = 1
    BulletStyle_FilledRoundBullet = 2
    BulletStyle_HollowSquareBullet = 3
    BulletStyle_FilledSquareBullet = 4
    BulletStyle_DashBullet = 5
    BulletStyle_Other = -1


class CapStyle(IntEnum):
    CapStyle_None = 0
    CapStyle_SmallCap = 1
    CapStyle_AllCap = 2
    CapStyle_AllPetiteCaps = 3
    CapStyle_PetiteCaps = 4
    CapStyle_Unicase = 5
    CapStyle_Titling = 6
    CapStyle_Other = -1


class CaretBidiMode(IntEnum):
    CaretBidiMode_LTR = 0
    CaretBidiMode_RTL = 1


class CaretPosition(IntEnum):
    CaretPosition_Unknown = 0
    CaretPosition_EndOfLine = 1
    CaretPosition_BeginningOfLine = 2


class CoalesceEventsOptions(IntFlag):
    CoalesceEventsOptions_Disabled = 0
    CoalesceEventsOptions_Enabled = 1


class ConditionType(IntEnum):
    ConditionType_True = 0
    ConditionType_False = 1
    ConditionType_Property = 2
    ConditionType_And = 3
    ConditionType_Or = 4
    ConditionType_Not = 5


class ConnectionRecoveryBehaviorOptions(IntFlag):
    ConnectionRecoveryBehaviorOptions_Disabled = 0
    ConnectionRecoveryBehaviorOptions_Enabled = 1


class EventArgsType(IntEnum):
    EventArgsType_Simple = 0
    EventArgsType_PropertyChanged = 1
    EventArgsType_StructureChanged = 2
    EventArgsType_AsyncContentLoaded = 3
    EventArgsType_WindowClosed = 4
    EventArgsType_TextEditTextChanged = 5
    EventArgsType_Changes = 6
    EventArgsType_Notification = 7
    EventArgsType_ActiveTextPositionChanged = 8
    EventArgsType_StructuredMarkup = 9


class FillType(IntEnum):
    FillType_None = 0
    FillType_Color = 1
    FillType_Gradient = 2
    FillType_Picture = 3
    FillType_Pattern = 4


class FlowDirections(IntEnum):
    FlowDirections_Default = 0
    FlowDirections_RightToLeft = 1
    FlowDirections_BottomToTop = 2
    FlowDirections_Vertical = 4


class LiveSetting(IntEnum):
    Off = 0
    Polite = 1
    Assertive = 2


class NormalizeState(IntEnum):
    NormalizeState_None = 0
    NormalizeState_View = 1
    NormalizeState_Custom = 2


class NotificationKind(IntEnum):
    NotificationKind_ItemAdded = 0
    NotificationKind_ItemRemoved = 1
    NotificationKind_ActionCompleted = 2
    NotificationKind_ActionAborted = 3
    NotificationKind_Other = 4


class NotificationProcessing(IntEnum):
    NotificationProcessing_ImportantAll = 0
    NotificationProcessing_ImportantMostRecent = 1
    NotificationProcessing_All = 2
    NotificationProcessing_MostRecent = 3
    NotificationProcessing_CurrentThenMostRecent = 4
    NotificationProcessing_ImportantCurrentThenMostRecent = 5


class OutlineStyles(IntEnum):
    OutlineStyles_None = 0
    OutlineStyles_Outline = 1
    OutlineStyles_Shadow = 2
    OutlineStyles_Engraved = 4
    OutlineStyles_Embossed = 8


class PropertyConditionFlags(IntFlag):
    PropertyConditionFlags_None = 0
    PropertyConditionFlags_IgnoreCase = 1
    PropertyConditionFlags_MatchSubstring = 2


class ProviderOptions(IntFlag):
    ProviderOptions_ClientSideProvider = 1
    ProviderOptions_ServerSideProvider = 2
    ProviderOptions_NonClientAreaProvider = 4
    ProviderOptions_OverrideProvider = 8
    ProviderOptions_ProviderOwnsSetFocus = 16
    ProviderOptions_UseComThreading = 32
    ProviderOptions_RefuseNonClientSupport = 64
    ProviderOptions_HasNativeIAccessible = 128
    ProviderOptions_UseClientCoordinates = 256


class ProviderType(IntEnum):
    ProviderType_BaseHwnd = 0
    ProviderType_Proxy = 1
    ProviderType_NonClientArea = 2


class SayAsInterpretAs(IntEnum):
    SayAsInterpretAs_None = 0
    SayAsInterpretAs_Spell = 1
    SayAsInterpretAs_Cardinal = 2
    SayAsInterpretAs_Ordinal = 3
    SayAsInterpretAs_Number = 4
    SayAsInterpretAs_Date = 5
    SayAsInterpretAs_Time = 6
    SayAsInterpretAs_Telephone = 7
    SayAsInterpretAs_Currency = 8
    SayAsInterpretAs_Net = 9
    SayAsInterpretAs_Url = 10
    SayAsInterpretAs_Address = 11
    SayAsInterpretAs_Alphanumeric = 12
    SayAsInterpretAs_Name = 13
    SayAsInterpretAs_Media = 14
    SayAsInterpretAs_Date_MonthDayYear = 15
    SayAsInterpretAs_Date_DayMonthYear = 16
    SayAsInterpretAs_Date_YearMonthDay = 17
    SayAsInterpretAs_Date_YearMonth = 18
    SayAsInterpretAs_Date_MonthYear = 19
    SayAsInterpretAs_Date_DayMonth = 20
    SayAsInterpretAs_Date_MonthDay = 21
    SayAsInterpretAs_Date_Year = 22
    SayAsInterpretAs_Time_HoursMinutesSeconds12 = 23
    SayAsInterpretAs_Time_HoursMinutes12 = 24
    SayAsInterpretAs_Time_HoursMinutesSeconds24 = 25
    SayAsInterpretAs_Time_HoursMinutes24 = 26


class StructureChangeType(IntEnum):
    StructureChangeType_ChildAdded = 0
    StructureChangeType_ChildRemoved = 1
    StructureChangeType_ChildrenInvalidated = 2
    StructureChangeType_ChildrenBulkAdded = 3
    StructureChangeType_ChildrenBulkRemoved = 4
    StructureChangeType_ChildrenReordered = 5


class SupportedTextSelection(IntEnum):
    SupportedTextSelection_None = 0
    SupportedTextSelection_Single = 1
    SupportedTextSelection_Multiple = 2


class SynchronizedInputType(IntEnum):
    SynchronizedInputType_KeyUp = 1
    SynchronizedInputType_KeyDown = 2
    SynchronizedInputType_LeftMouseUp = 4
    SynchronizedInputType_LeftMouseDown = 8
    SynchronizedInputType_RightMouseUp = 16
    SynchronizedInputType_RightMouseDown = 32


class TextDecorationLineStyle(IntEnum):
    TextDecorationLineStyle_None = 0
    TextDecorationLineStyle_Single = 1
    TextDecorationLineStyle_WordsOnly = 2
    TextDecorationLineStyle_Double = 3
    TextDecorationLineStyle_Dot = 4
    TextDecorationLineStyle_Dash = 5
    TextDecorationLineStyle_DashDot = 6
    TextDecorationLineStyle_DashDotDot = 7
    TextDecorationLineStyle_Wavy = 8
    TextDecorationLineStyle_ThickSingle = 9
    TextDecorationLineStyle_DoubleWavy = 11
    TextDecorationLineStyle_ThickWavy = 12
    TextDecorationLineStyle_LongDash = 13
    TextDecorationLineStyle_ThickDash = 14
    TextDecorationLineStyle_ThickDashDot = 15
    TextDecorationLineStyle_ThickDashDotDot = 16
    TextDecorationLineStyle_ThickDot = 17
    TextDecorationLineStyle_ThickLongDash = 18
    TextDecorationLineStyle_Other = -1


class TextEditChangeType(IntEnum):
    TextEditChangeType_None = 0
    TextEditChangeType_AutoCorrect = 1
    TextEditChangeType_Composition = 2
    TextEditChangeType_CompositionFinalized = 3
    TextEditChangeType_AutoComplete = 4


class TreeScope(IntEnum):
    TreeScope_None = 0
    TreeScope_Element = 1
    TreeScope_Children = 2
    TreeScope_Descendants = 4
    TreeScope_Parent = 8
    TreeScope_Ancestors = 16
    TreeScope_Subtree = 7


class TreeTraversalOptions(IntFlag):
    TreeTraversalOptions_Default = 0
    TreeTraversalOptions_PostOrder = 1
    TreeTraversalOptions_LastToFirstOrder = 2


class UIAutomationType(IntEnum):
    UIAutomationType_Int = 1
    UIAutomationType_Bool = 2
    UIAutomationType_String = 3
    UIAutomationType_Double = 4
    UIAutomationType_Point = 5
    UIAutomationType_Rect = 6
    UIAutomationType_Element = 7
    UIAutomationType_Array = 65536
    UIAutomationType_Out = 131072
    UIAutomationType_IntArray = 131073
    UIAutomationType_BoolArray = 131074
    UIAutomationType_StringArray = 131075
    UIAutomationType_DoubleArray = 131076
    UIAutomationType_PointArray = 131077
    UIAutomationType_RectArray = 131078
    UIAutomationType_ElementArray = 131079
    UIAutomationType_OutInt = 131080
    UIAutomationType_OutBool = 131081
    UIAutomationType_OutString = 131082
    UIAutomationType_OutDouble = 131083
    UIAutomationType_OutPoint = 131084
    UIAutomationType_OutRect = 131085
    UIAutomationType_OutElement = 131086
    UIAutomationType_OutIntArray = 131087
    UIAutomationType_OutBoolArray = 131088
    UIAutomationType_OutStringArray = 131089
    UIAutomationType_OutDoubleArray = 131090
    UIAutomationType_OutPointArray = 131091
    UIAutomationType_OutRectArray = 131092
    UIAutomationType_OutElementArray = 131093


class VisualEffects(IntEnum):
    VisualEffects_None = 0
    VisualEffects_Shadow = 1
    VisualEffects_Reflection = 2
    VisualEffects_Glow = 3
    VisualEffects_SoftEdges = 4
    VisualEffects_Bevel = 5


class UIAError(IntEnum):
    """UI Automation and COM error codes.

    Source: Winerror.h, UIAutomationCoreApi.h, WinError.h.
    Categories:
      UIA_E_*     — UI Automation–specific errors
      RO_E_*      — Windows Runtime object lifecycle errors
      RPC_E_*     — COM RPC transport/threading errors
      CO_E_*      — COM object state errors
      E_*         — General COM errors
    """

    UIA_E_ELEMENTNOTENABLED = -2147220992  # 0x80040200 — method called on disabled element
    UIA_E_ELEMENTNOTAVAILABLE = -2147220991  # 0x80040201 — element destroyed or virtualized
    UIA_E_NOCLICKABLEPOINT = -2147220990  # 0x80040202 — element has no clickable point
    UIA_E_PROXYASSEMBLYNOTLOADED = -2147220989  # 0x80040203 — client-side proxy provider failed to load
    UIA_E_NOTSUPPORTED = -2147220988  # 0x80040204 — property/pattern not supported by provider
    UIA_E_INVALIDOPERATION = -2146233079  # 0x80131509 — operation not valid in current state
    UIA_E_TIMEOUT = -2146233083  # 0x80131505 — UIA operation timed out

    EVENT_E_ALL_SUBSCRIBERS_FAILED = -2147220991  # 0x80040201 — same as UIA_E_ELEMENTNOTAVAILABLE

    RO_E_CLOSED = -2147483629  # 0x80000013 — object has been closed/disposed

    RPC_E_CALL_REJECTED = -2147418111  # 0x80010001 — callee rejected the call (app busy, may retry)
    RPC_E_CALL_CANCELED = -2147418110  # 0x80010002 — call canceled by message filter
    RPC_E_CONNECTION_TERMINATED = -2147418106  # 0x80010006 — connection terminated or in bogus state
    RPC_E_SERVER_DIED = -2147418105  # 0x80010007 — server gone, call may have executed
    RPC_E_SERVER_DIED_DNE = -2147418094  # 0x80010012 — server gone, call did NOT execute
    RPC_E_SYS_CALL_FAILED = -2147417854  # 0x80010100 — underlying system call failed
    RPC_E_OUT_OF_RESOURCES = -2147417855  # 0x80010101 — could not allocate required resource
    RPC_E_ATTEMPTED_MULTITHREAD = -2147417854  # 0x80010102 — multiple threads in STA mode
    RPC_E_SERVERFAULT = -2147417851  # 0x80010105 — server threw an exception
    RPC_E_CHANGED_MODE = -2147417850  # 0x80010106 — STA/MTA mode conflict on thread
    RPC_E_DISCONNECTED = -2147418360  # 0x80010108 — object invoked has disconnected from clients
    RPC_E_SERVERCALL_RETRYLATER = -2147417846  # 0x8001010A — app busy, retry later
    RPC_E_SERVERCALL_REJECTED = -2147417845  # 0x8001010B — message filter rejected the call
    RPC_E_WRONG_THREAD = -2147417842  # 0x8001010E — interface marshalled for a different thread
    RPC_E_THREAD_NOT_INIT = -2147417841  # 0x8001010F — CoInitialize not called on current thread
    RPC_E_TIMEOUT = -2147417825  # 0x8001011F — RPC-level operation timed out
    RPC_E_UNEXPECTED = -2147352577  # 0x8001FFFF — internal RPC error
    RPC_E_ACCESS_DENIED = -2147417829  # 0x8001011B — RPC-level access denied

    CO_E_OBJNOTCONNECTED = -2147220995  # 0x800401FD — object not connected to server
    CO_E_RELEASED = -2147220993  # 0x800401FF — object has been released
    CO_E_NOTINITIALIZED = -2147220496  # 0x800401F0 — CoInitialize not called

    E_ACCESSDENIED = -2147024891  # 0x80070005 — access denied


def is_dead_element_error(code: int) -> bool:
    """Check if error code indicates element or window no longer exists."""
    dead_codes = {
        UIAError.UIA_E_ELEMENTNOTAVAILABLE,
        UIAError.RO_E_CLOSED,
        UIAError.RPC_E_DISCONNECTED,
        UIAError.RPC_E_SERVER_DIED,
        UIAError.RPC_E_SERVER_DIED_DNE,
        UIAError.RPC_E_CONNECTION_TERMINATED,
        UIAError.CO_E_OBJNOTCONNECTED,
        UIAError.CO_E_RELEASED,
    }
    return code in dead_codes


def is_retryable_error(code: int) -> bool:
    """Check if error code indicates app is busy but alive — may succeed on retry."""
    retryable_codes = {
        UIAError.RPC_E_CALL_REJECTED,
        UIAError.RPC_E_SERVERCALL_RETRYLATER,
        UIAError.RPC_E_SERVERCALL_REJECTED,
    }
    return code in retryable_codes

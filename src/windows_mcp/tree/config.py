INTERACTIVE_CONTROL_TYPE_NAMES = set(
    [
        "ButtonControl",
        "ListItemControl",
        "MenuItemControl",
        "EditControl",
        "CheckBoxControl",
        "RadioButtonControl",
        "ComboBoxControl",
        "HyperlinkControl",
        "SplitButtonControl",
        "TabItemControl",
        "TreeItemControl",
        "DataItemControl",
        "HeaderItemControl",
        "TextBoxControl",
        "SpinnerControl",
        # Sliders were missing here even though INTERACTIVE_ROLES already lists "Slider"
        # and tree_traversal has a SliderControl branch that reads RangeValue into
        # metadata. The control-type gate runs before the role check, so every slider was
        # dropped and that branch was unreachable -- Settings' "Text size" slider
        # (value=100, max=225, focusable, 7216px) never reached the tree at all.
        # Chromium's zero-area "resize handle" sliders stay excluded by the area > 0 check.
        "SliderControl",
        "ScrollBarControl",
    ]
)

INTERACTIVE_ROLES = {
    # Buttons
    "PushButton",
    "SplitButton",
    "ButtonDropDown",
    "ButtonMenu",
    "ButtonDropDownGrid",
    "OutlineButton",
    # Links
    "Link",
    # Inputs & Selection
    "Text",
    "IpAddress",
    "HotkeyField",
    "ComboBox",
    "DropList",
    "CheckButton",
    "RadioButton",
    # Menus & Tabs
    "MenuItem",
    "ListItem",
    "PageTab",
    # Trees
    "OutlineItem",
    # Values
    "Slider",
    "SpinButton",
    "Dial",
    "ScrollBar",
    "Grip",
    # Grids
    "ColumnHeader",
    "RowHeader",
    "Cell",
}

DOCUMENT_CONTROL_TYPE_NAMES = set(["DocumentControl"])

STRUCTURAL_CONTROL_TYPE_NAMES = set([
    "PaneControl",
    "GroupControl",
    "CustomControl",
    "ToolBarControl",
    "TabControl",
    "MenuBarControl",
])

INFORMATIVE_CONTROL_TYPE_NAMES = set(
    [
        "TextControl",
        "ImageControl",
        "StatusBarControl",
        # 'ProgressBarControl',
        # 'ToolTipControl',
        # 'TitleBarControl',
        # 'SeparatorControl',
        # 'HeaderControl',
        # 'HeaderItemControl',
    ]
)

DEFAULT_ACTIONS = set(["Click", "Press", "Jump", "Check", "Uncheck", "Double Click"])

THREAD_MAX_RETRIES = 3

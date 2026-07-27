from types import SimpleNamespace

from windows_mcp.tree.views import (
    BoundingBox,
    Center,
    ScrollElementNode,
    SemanticNode,
    TreeElementNode,
    TreeState,
)


class TestBoundingBox:
    def test_get_center_standard(self, sample_bounding_box):
        center = sample_bounding_box.get_center()
        assert center.x == 200
        assert center.y == 100

    def test_get_center_zero_size(self):
        bb = BoundingBox(left=50, top=50, right=50, bottom=50, width=0, height=0)
        center = bb.get_center()
        assert center.x == 50
        assert center.y == 50

    def test_get_center_negative_coords(self):
        bb = BoundingBox(left=-100, top=-200, right=100, bottom=0, width=200, height=200)
        center = bb.get_center()
        assert center.x == 0
        assert center.y == -100

    def test_xywh_to_string(self, sample_bounding_box):
        assert sample_bounding_box.xywh_to_string() == "(100,50,200,100)"

    def test_xyxy_to_string(self, sample_bounding_box):
        assert sample_bounding_box.xyxy_to_string() == "(100,50,300,150)"

    def test_convert_xywh_to_xyxy(self, sample_bounding_box):
        x1, y1, x2, y2 = sample_bounding_box.convert_xywh_to_xyxy()
        assert (x1, y1, x2, y2) == (100, 50, 300, 150)

    def test_from_bounding_rectangle(self):
        mock_rect = SimpleNamespace(left=10, top=20, right=110, bottom=70)
        mock_rect.width = lambda: 100
        mock_rect.height = lambda: 50
        bb = BoundingBox.from_bounding_rectangle(mock_rect)
        assert bb.left == 10
        assert bb.top == 20
        assert bb.right == 110
        assert bb.bottom == 70
        assert bb.width == 100
        assert bb.height == 50


class TestCenter:
    def test_to_string_standard(self, sample_center):
        assert sample_center.to_string() == "(200,100)"

    def test_to_string_negative(self):
        c = Center(x=-10, y=-20)
        assert c.to_string() == "(-10,-20)"


class TestTreeState:
    def test_interactive_elements_to_string_empty(self):
        ts = TreeState()
        assert ts.interactive_elements_to_string() == "No interactive elements"

    def test_truncated_appends_note_to_interactive_elements(
        self, sample_tree_element_node: TreeElementNode
    ) -> None:
        ts = TreeState(
            interactive_nodes=[sample_tree_element_node], truncated=True, element_limit=500
        )
        result = ts.interactive_elements_to_string()
        assert "500-element capture limit" in result
        assert "WINDOWS_MCP_MAX_TREE_ELEMENTS" in result

    def test_truncated_appends_note_to_scrollable_elements(
        self, sample_scroll_element_node: ScrollElementNode
    ) -> None:
        ts = TreeState(scrollable_nodes=[sample_scroll_element_node], truncated=True, element_limit=42)
        result = ts.scrollable_elements_to_string()
        assert "42-element capture limit" in result

    def test_not_truncated_omits_note(self, sample_tree_element_node: TreeElementNode) -> None:
        ts = TreeState(interactive_nodes=[sample_tree_element_node], truncated=False)
        result = ts.interactive_elements_to_string()
        assert "truncated" not in result

    def test_truncated_with_no_elements_still_shows_note(self) -> None:
        ts = TreeState(truncated=True, element_limit=10)
        result = ts.interactive_elements_to_string()
        assert result == "No interactive elements"

    def test_truncated_appends_note_to_semantic_tree(self) -> None:
        root = SemanticNode(control_type="Desktop", element_type="desktop", name="Desktop")
        window = SemanticNode(control_type="Window", element_type="window", name="Notepad")
        root.add_child(window)
        ts = TreeState(semantic_tree_root=root, truncated=True, element_limit=500)
        result = ts.semantic_tree_to_string()
        assert 'window "Notepad"' in result
        assert "500-element capture limit" in result

    def test_semantic_tree_not_truncated_omits_note(self) -> None:
        root = SemanticNode(control_type="Desktop", element_type="desktop", name="Desktop")
        ts = TreeState(semantic_tree_root=root, truncated=False)
        result = ts.semantic_tree_to_string()
        assert "truncated" not in result

    def test_interactive_elements_to_string_with_elements(
        self, sample_tree_element_node: TreeElementNode
    ) -> None:
        ts = TreeState(interactive_nodes=[sample_tree_element_node])
        result = ts.interactive_elements_to_string()
        lines = result.split("\n")
        assert lines[0] == "window \"Notepad\""
        assert (
            lines[1]
            == "\u2514\u2500\u2500 (200,100) button \"OK\"  [action: click]  "
            "[focused]  [shortcut:Alt+O]"
        )

    def test_interactive_elements_preserve_order(
        self, sample_tree_element_node: TreeElementNode
    ) -> None:
        node2 = TreeElementNode(
            bounding_box=sample_tree_element_node.bounding_box,
            center=sample_tree_element_node.center,
            name="Cancel",
            control_type="Button",
            window_name="Notepad",
        )
        ts = TreeState(interactive_nodes=[sample_tree_element_node, node2])
        result = ts.interactive_elements_to_string()
        lines = result.split("\n")
        assert (
            lines[1]
            == "\u251c\u2500\u2500 (200,100) button \"OK\"  [action: click]  "
            "[focused]  [shortcut:Alt+O]"
        )
        assert lines[2] == "\u2514\u2500\u2500 (200,100) button \"Cancel\"  [action: click]"

    def test_scrollable_elements_to_string_empty(self):
        ts = TreeState()
        assert ts.scrollable_elements_to_string() == "No scrollable elements"

    def test_scrollable_elements_to_string_with_elements(
        self,
        sample_scroll_element_node: ScrollElementNode,
        sample_tree_element_node: TreeElementNode,
    ) -> None:
        ts = TreeState(
            interactive_nodes=[sample_tree_element_node],
            scrollable_nodes=[sample_scroll_element_node],
        )
        result = ts.scrollable_elements_to_string()
        lines = result.split("\n")
        assert lines[0] == "window \"Notepad\""
        assert (
            lines[1]
            == "\u2514\u2500\u2500 (200,100) pane \"Document\"  [action: click]  [v:42.5%]"
        )

    def test_scrollable_elements_are_independent_from_interactive_count(
        self, sample_scroll_element_node: ScrollElementNode
    ) -> None:
        bb = BoundingBox(left=0, top=0, right=10, bottom=10, width=10, height=10)
        c = Center(x=5, y=5)
        interactive = [TreeElementNode(bounding_box=bb, center=c, name=f"btn{i}") for i in range(3)]
        ts = TreeState(
            interactive_nodes=interactive,
            scrollable_nodes=[sample_scroll_element_node],
        )
        result = ts.scrollable_elements_to_string()
        lines = result.split("\n")
        assert lines[0] == "window \"Notepad\""
        assert (
            lines[1]
            == "\u2514\u2500\u2500 (200,100) pane \"Document\"  [action: click]  [v:42.5%]"
        )


class TestTreeElementNode:
    def test_update_from_node(self, sample_tree_element_node):
        target = TreeElementNode(
            bounding_box=BoundingBox(left=0, top=0, right=0, bottom=0, width=0, height=0),
            center=Center(x=0, y=0),
        )
        target.update_from_node(sample_tree_element_node)
        assert target.name == "OK"
        assert target.control_type == "Button"
        assert target.window_name == "Notepad"
        assert target.metadata["value"] == ""
        assert target.metadata["shortcut"] == "Alt+O"
        assert target.metadata["has_focused"] is True
        assert target.bounding_box is sample_tree_element_node.bounding_box
        assert target.center is sample_tree_element_node.center

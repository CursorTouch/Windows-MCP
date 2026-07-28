from windows_mcp.tree.budget import (
    DEFAULT_MAX_TREE_ELEMENTS,
    TreeElementBudget,
    resolve_max_tree_elements,
)


class TestTreeElementBudget:
    def test_starts_unexhausted(self):
        budget = TreeElementBudget(limit=3)
        assert budget.exhausted is False
        assert budget.truncated is False
        assert budget.remaining == 3

    def test_try_consume_under_limit(self):
        budget = TreeElementBudget(limit=3)
        assert budget.try_consume() is True
        assert budget.count == 1
        assert budget.truncated is False

    def test_try_consume_reaching_limit_marks_truncated(self):
        budget = TreeElementBudget(limit=2)
        assert budget.try_consume() is True
        assert budget.try_consume() is True
        assert budget.exhausted is True
        assert budget.truncated is True

    def test_try_consume_beyond_limit_returns_false(self):
        budget = TreeElementBudget(limit=1)
        assert budget.try_consume() is True
        assert budget.try_consume() is False
        assert budget.count == 1
        assert budget.truncated is True

    def test_try_consume_batch_exceeding_remaining(self):
        budget = TreeElementBudget(limit=5)
        assert budget.try_consume(3) is True
        assert budget.try_consume(10) is False
        assert budget.count == 5
        assert budget.truncated is True

    def test_try_consume_zero_amount_does_not_change_count(self):
        budget = TreeElementBudget(limit=5)
        assert budget.try_consume(0) is True
        assert budget.count == 0

    def test_limit_is_clamped_to_at_least_one(self):
        budget = TreeElementBudget(limit=0)
        assert budget.limit == 1

    def test_remaining_never_negative(self):
        budget = TreeElementBudget(limit=2)
        budget.try_consume(10)
        assert budget.remaining == 0
        assert budget.count == 2


class TestResolveMaxTreeElements:
    def test_default_when_unset(self):
        assert resolve_max_tree_elements({}) == DEFAULT_MAX_TREE_ELEMENTS

    def test_default_when_blank(self):
        assert (
            resolve_max_tree_elements({"WINDOWS_MCP_MAX_TREE_ELEMENTS": "  "})
            == DEFAULT_MAX_TREE_ELEMENTS
        )

    def test_valid_override(self):
        assert resolve_max_tree_elements({"WINDOWS_MCP_MAX_TREE_ELEMENTS": "150"}) == 150

    def test_invalid_value_falls_back_to_default(self):
        assert (
            resolve_max_tree_elements({"WINDOWS_MCP_MAX_TREE_ELEMENTS": "not-a-number"})
            == DEFAULT_MAX_TREE_ELEMENTS
        )

    def test_zero_falls_back_to_default(self):
        assert resolve_max_tree_elements({"WINDOWS_MCP_MAX_TREE_ELEMENTS": "0"}) == DEFAULT_MAX_TREE_ELEMENTS

    def test_negative_falls_back_to_default(self):
        assert resolve_max_tree_elements({"WINDOWS_MCP_MAX_TREE_ELEMENTS": "-5"}) == DEFAULT_MAX_TREE_ELEMENTS

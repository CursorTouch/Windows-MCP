"""
UIA Caching Utilities for Performance Optimization

This module provides utilities for implementing UI Automation caching
to reduce cross-process COM calls during tree traversal.
"""

from windows_mcp.uia import CacheRequest, PropertyId, PatternId, TreeScope, Control
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class CacheRequestFactory:
    """Factory for creating optimized cache requests for different scenarios."""

    @staticmethod
    def create_tree_traversal_cache() -> CacheRequest:
        """
        Creates a cache request optimized for tree traversal.
        Caches all commonly accessed properties and patterns.

        This cache request is designed to minimize COM calls during
        the tree_traversal() operation in tree/service.py.

        Returns:
            CacheRequest configured for tree traversal
        """
        cache_request = CacheRequest()

        # Set scope to cache element and its children
        # This allows us to get children with pre-cached properties
        cache_request.TreeScope = TreeScope.TreeScope_Element | TreeScope.TreeScope_Children

        # Basic identification properties
        cache_request.AddProperty(PropertyId.NameProperty)
        cache_request.AddProperty(PropertyId.AutomationIdProperty)
        cache_request.AddProperty(PropertyId.LocalizedControlTypeProperty)
        cache_request.AddProperty(PropertyId.AcceleratorKeyProperty)
        cache_request.AddProperty(PropertyId.ClassNameProperty)
        cache_request.AddProperty(PropertyId.ControlTypeProperty)

        # State properties for visibility and interaction checks
        cache_request.AddProperty(PropertyId.IsEnabledProperty)
        cache_request.AddProperty(PropertyId.IsOffscreenProperty)
        cache_request.AddProperty(PropertyId.IsControlElementProperty)
        cache_request.AddProperty(PropertyId.HasKeyboardFocusProperty)
        cache_request.AddProperty(PropertyId.IsKeyboardFocusableProperty)
        cache_request.AddProperty(PropertyId.IsPasswordProperty)

        # Layout properties
        cache_request.AddProperty(PropertyId.BoundingRectangleProperty)
        cache_request.AddProperty(PropertyId.HelpTextProperty)

        # Patterns
        cache_request.AddPattern(PatternId.LegacyIAccessiblePattern)
        cache_request.AddPattern(PatternId.ScrollPattern)
        cache_request.AddPattern(PatternId.WindowPattern)

        # LegacyIAccessible properties
        cache_request.AddProperty(PropertyId.LegacyIAccessibleRoleProperty)
        cache_request.AddProperty(PropertyId.LegacyIAccessibleValueProperty)
        cache_request.AddProperty(PropertyId.LegacyIAccessibleDefaultActionProperty)
        cache_request.AddProperty(PropertyId.LegacyIAccessibleStateProperty)

        # Scroll properties
        cache_request.AddProperty(PropertyId.ScrollHorizontallyScrollableProperty)
        cache_request.AddProperty(PropertyId.ScrollVerticallyScrollableProperty)
        cache_request.AddProperty(PropertyId.ScrollHorizontalScrollPercentProperty)
        cache_request.AddProperty(PropertyId.ScrollVerticalScrollPercentProperty)

        # ExpandCollapse properties
        cache_request.AddProperty(PropertyId.ExpandCollapseExpandCollapseStateProperty)

        # Selection properties
        cache_request.AddProperty(PropertyId.SelectionCanSelectMultipleProperty)
        cache_request.AddProperty(PropertyId.SelectionIsSelectionRequiredProperty)
        cache_request.AddProperty(PropertyId.SelectionSelectionProperty)

        # SelectionItem properties
        cache_request.AddProperty(PropertyId.SelectionItemIsSelectedProperty)
        cache_request.AddProperty(PropertyId.SelectionItemSelectionContainerProperty)

        # Window properties
        cache_request.AddProperty(PropertyId.WindowIsModalProperty)

        # Toggle properties (ButtonControl, CheckBoxControl)
        cache_request.AddProperty(PropertyId.ToggleToggleStateProperty)

        # RangeValue properties (SliderControl)
        cache_request.AddProperty(PropertyId.RangeValueValueProperty)
        cache_request.AddProperty(PropertyId.RangeValueMinimumProperty)
        cache_request.AddProperty(PropertyId.RangeValueMaximumProperty)

        return cache_request


class CachedControlHelper:
    """Helper class for working with cached controls."""

    @staticmethod
    def build_cached_control(
        node: Control, cache_request: Optional[CacheRequest] = None
    ) -> Control:
        """
        Build a cached version of a control.

        Args:
            node: The control to cache
            cache_request: Optional custom cache request. If None, uses tree traversal cache.

        Returns:
            A control with cached properties, or the original control if caching fails
        """
        if cache_request is None:
            cache_request = CacheRequestFactory.create_tree_traversal_cache()

        try:
            cached_node = node.BuildUpdatedCache(cache_request)
            cached_node._is_cached = True
            return cached_node
        except Exception as e:
            logger.debug(f"Failed to build cached control: {e}")
            return node

    @staticmethod
    def get_cached_children(
        node: Control, cache_request: Optional[CacheRequest] = None
    ) -> list[Control]:
        """
        Get children with pre-cached properties.

        This is the most significant optimization - it retrieves all children
        with their properties already cached, eliminating the need for individual
        property access calls on each child.

        Args:
            node: The parent control
            cache_request: Optional custom cache request. If None, uses tree traversal cache.

        Returns:
            List of children with cached properties
        """
        if cache_request is None:
            cache_request = CacheRequestFactory.create_tree_traversal_cache()

        # Optimization: To avoid redundant COM property fetches for the parent node,
        # we only need the Children scope. If TreeScope_Element is omitted,
        # UI Automation only fetches the children and their properties.
        # We clone the request to avoid modifying shared cache request objects.
        req_clone = cache_request.Clone()
        req_clone.TreeScope = TreeScope.TreeScope_Children

        try:
            # Bypass `Control.CreateControlFromElement` when building the cache.
            # When TreeScope_Element is omitted, BuildUpdatedCache returns a dummy element
            # which does not have valid properties (like CurrentControlType),
            # so our python wrapper would crash. We just use the raw IUIAutomationElement.
            updated_element = node.Element.BuildUpdatedCache(req_clone.check_request)
            element_array = updated_element.GetCachedChildren()

            children = []
            if element_array:
                length = element_array.Length
                for i in range(length):
                    child_elem = element_array.GetElement(i)
                    child_control = Control.CreateControlFromElement(child_elem)
                    if child_control:
                        child_control._is_cached = True
                        children.append(child_control)

            logger.debug(f"Retrieved {len(children)} cached children (newly built)")
            return children

        except Exception as e:
            logger.debug(f"Failed to get cached children, falling back to regular access: {e}")
            return node.GetChildren()

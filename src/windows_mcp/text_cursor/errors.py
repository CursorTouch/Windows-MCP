"""Errors raised by text cursor operations."""


class TextCursorError(RuntimeError):
    """Base error raised by TextCursor operations."""


class TextCursorVerificationError(TextCursorError):
    """Raised when a TextCursor write cannot be verified."""

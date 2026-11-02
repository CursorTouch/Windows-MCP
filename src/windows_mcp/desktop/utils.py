"""Desktop utilities. Input sanitization and text processing helpers."""

import json
import os
import re

import pywintypes
from win32com.shell import shell

__all__ = [
    "resolve_known_folder_guid_path",
    "remove_private_use_chars",
    "repair_surrogates",
    "is_elevated",
    "as_bool",
    "as_int_list",
]


def as_bool(value: bool | str, name: str) -> bool:
    """Parse a tool argument declared as ``boolean | string`` into a bool.

    Claude Desktop strips anyOf schemas, so a caller may send "true" where the
    schema says true. Only the two literals are accepted: anything else raises
    rather than defaulting to false, so a typo or a plausible-looking "yes"
    cannot silently turn a requested action into a no-op.

    Args:
        value: The raw argument value.
        name: Parameter name, used in the error message.

    Returns:
        The parsed boolean.

    Raises:
        ValueError: If *value* is neither a bool nor "true"/"false".
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(f"{name} must be true or false")


def as_int_list(value: list[int] | str | None, name: str) -> list[int] | None:
    """Parse a tool argument declared as ``array | string`` into a list of ints.

    Some MCP clients strip anyOf schemas, so a caller may send "[0, 1]" where
    the schema says [0, 1]. A string must hold a JSON array of integers:
    anything else raises rather than reaching the tool as a string, a number
    or a list of other values. JSON true and false do not count as integers
    here.

    Args:
        value: The raw argument value.
        name: Parameter name, used in the error message.

    Returns:
        The list, or None when the argument was omitted.

    Raises:
        ValueError: If *value* is a string that is not a JSON array of integers.
    """
    if value is None or isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = None
        if isinstance(parsed, list) and all(type(item) is int for item in parsed):
            return parsed
    raise ValueError(f"{name} must be a JSON array of integers")


def is_elevated() -> bool:
    """Check if the current process has administrative privileges."""
    import ctypes
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except (AttributeError, Exception):
        # Not on Windows or Win32 API unavailable
        return False


_GUID_PATH_RE = re.compile(r"^\{([0-9A-Fa-f-]{36})}(?:\\(.*))?$")


def resolve_known_folder_guid_path(path_text: str) -> str:
    """Resolve a Windows Known Folder GUID path to an absolute filesystem path.

    Some Start Menu shortcuts store their target as a GUID-based path such as
    ``{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\msinfo32.exe``,
    where the leading ``{...}`` is a Known Folder ID (e.g. the Windows directory).
    ``Start-Process`` cannot launch these paths directly, so this function calls
    ``SHGetKnownFolderPath`` to resolve the GUID to its actual location.

    Args:
        path_text: A raw path string, possibly prefixed with a Known Folder GUID.

    Returns:
        The resolved absolute path if the GUID is valid, or *path_text* unchanged
        if it does not match the ``{GUID}\\...`` pattern or the GUID is unrecognised.
    """
    m = _GUID_PATH_RE.match(path_text)
    if not m:
        return path_text

    guid_text = "{" + m.group(1) + "}"
    rest = m.group(2)
    try:
        folder_id = pywintypes.IID(guid_text)
        base = shell.SHGetKnownFolderPath(folder_id, 0, 0)
    except Exception:
        # If the GUID is not a known folder id, just return the original text
        return path_text

    return base if not rest else os.path.join(base, rest)


_PRIVATE_USE_RE = re.compile(
    r'['
    r'\uE000-\uF8FF'          # BMP Private Use Area
    r'\U000F0000-\U000FFFFD'  # Supplementary Private Use Area-A
    r'\U00100000-\U0010FFFD'  # Supplementary Private Use Area-B
    r']+'
)


def remove_private_use_chars(text: str) -> str:
    """Remove Unicode Private Use Area characters that may cause rendering issues."""
    return _PRIVATE_USE_RE.sub('', text)


_SURROGATE_RE = re.compile(r'[\ud800-\udfff]')


def repair_surrogates(text: str) -> str:
    """Combine UTF-16 surrogate pairs into real characters, replacing unpaired ones.

    UIA hands back UTF-16 text, and an astral character such as an emoji can
    arrive as its raw surrogate pair (U+1F437 as U+D83D U+DC37) instead of a
    single code point. Python keeps those surrogates in the str quite happily,
    but encoding one to UTF-8 raises UnicodeEncodeError -- so the whole tool
    response fails to serialize and the caller gets nothing back at all, over a
    single emoji in somebody's window title.

    Pair up what can be paired, and replace what cannot with U+FFFD, so a stray
    half-character costs one glyph rather than the entire snapshot.
    """
    if not _SURROGATE_RE.search(text):
        return text

    out: list[str] = []
    i = 0
    end = len(text)
    while i < end:
        code = ord(text[i])
        if 0xD800 <= code <= 0xDBFF and i + 1 < end:
            low = ord(text[i + 1])
            if 0xDC00 <= low <= 0xDFFF:
                out.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                i += 2
                continue
        out.append('\ufffd' if 0xD800 <= code <= 0xDFFF else text[i])
        i += 1
    return ''.join(out)

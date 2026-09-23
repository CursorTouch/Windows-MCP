"""Network capabilities for Windows MCP."""

from windows_mcp.net.service import (
    ftp_connect,
    ftp_disconnect,
    ftp_operation,
    http_download,
    http_request,
    imap_operation,
    smtp_send,
    webhook_send,
)

__all__ = [
    "ftp_connect",
    "ftp_disconnect",
    "ftp_operation",
    "http_download",
    "http_request",
    "imap_operation",
    "smtp_send",
    "webhook_send",
]

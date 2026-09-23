"""Net tool - HTTP, email, FTP, and enterprise chat webhooks."""

from typing import Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics

from windows_mcp.net import (
    ftp_connect as ftp_connect_service,
    ftp_disconnect as ftp_disconnect_service,
    ftp_operation as ftp_operation_service,
    http_download as http_download_service,
    http_request as http_request_service,
    imap_operation as imap_operation_service,
    smtp_send as smtp_send_service,
    webhook_send as webhook_send_service,
)


def _bool(value: Any, default: bool = False) -> bool:
    """Normalize MCP boolean strings."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="Net",
        description=(
            "Network operations. Keywords: HTTP, REST, API, download, resume, SMTP, send email, "
            "IMAP, receive email, attachments, FTP, FTPS, upload, webhook, WeCom, DingTalk, Feishu. "
            "Modes: http, download, smtp_send, imap, ftp_connect, ftp, ftp_disconnect, webhook."
        ),
        annotations=ToolAnnotations(
            title="Net",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_analytics(get_analytics(), "Net-Tool")
    def net_tool(
        mode: Literal[
            "http",
            "download",
            "smtp_send",
            "imap",
            "ftp_connect",
            "ftp",
            "ftp_disconnect",
            "webhook",
        ],
        url: str | None = None,
        destination: str | None = None,
        method: str = "GET",
        headers: dict[str, Any] | str | None = None,
        params: dict[str, Any] | str | None = None,
        json_payload: Any = None,
        form_data: dict[str, Any] | str | None = None,
        raw_body: str | bytes | None = None,
        timeout: float | str = 30,
        allow_redirects: bool | str = True,
        verify: bool | str = True,
        max_text_length: int | str = 200_000,
        overwrite: bool | str = False,
        resume: bool | str = True,
        expected_size: int | str | None = None,
        host: str | None = None,
        port: int | str | None = None,
        username: str | None = None,
        password: str | None = None,
        sender: str | None = None,
        recipients: str | list[str] | None = None,
        cc: str | list[str] | None = None,
        bcc: str | list[str] | None = None,
        subject: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        attachments: str | list[str] | None = None,
        use_ssl: bool | str = True,
        starttls: bool | str = False,
        mailbox: str = "INBOX",
        operation: str | None = None,
        search_criteria: str | list[str] | None = None,
        limit: int | str = 20,
        uid: str | int | None = None,
        save_attachments_dir: str | None = None,
        mark_seen: bool | str = False,
        connection_id: str | None = None,
        remote_path: str | None = None,
        new_remote_path: str | None = None,
        platform: str | None = None,
        message: str | None = None,
        msg_type: str = "text",
        title: str | None = None,
        at_user_ids: str | list[str] | None = None,
        at_mobiles: str | list[str] | None = None,
        secret: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Dispatch a network operation and return a human-readable result or Error string.

        Credentials are accepted only as arguments for this call. SQL-like injection is not
        relevant here; HTTP URLs and local paths are validated by the service layer.
        """
        try:
            if mode == "http":
                if not url:
                    return "Error: url is required for http mode."
                return http_request_service(
                    url=url,
                    method=method,
                    headers=headers,
                    params=params,
                    json_payload=json_payload,
                    form_data=form_data,
                    raw_body=raw_body,
                    timeout=float(timeout),
                    allow_redirects=_bool(allow_redirects, True),
                    verify=_bool(verify, True),
                    max_text_length=int(max_text_length),
                )
            if mode == "download":
                if not url or not destination:
                    return "Error: url and destination are required for download mode."
                return http_download_service(
                    url=url,
                    destination=destination,
                    overwrite=_bool(overwrite),
                    resume=_bool(resume, True),
                    expected_size=(int(expected_size) if expected_size not in (None, "") else None),
                    timeout=float(timeout),
                    headers=headers,
                    allow_redirects=_bool(allow_redirects, True),
                )
            if mode == "smtp_send":
                if not host or not sender or not recipients or subject is None:
                    return (
                        "Error: host, sender, recipients, and subject are required for smtp_send."
                    )
                return smtp_send_service(
                    host=host,
                    sender=sender,
                    recipients=recipients,
                    cc=cc,
                    bcc=bcc,
                    subject=subject,
                    body_text=body_text or "",
                    body_html=body_html,
                    attachments=attachments,
                    username=username,
                    password=password,
                    port=int(port) if port not in (None, "") else None,
                    use_ssl=_bool(use_ssl, True),
                    starttls=_bool(starttls),
                    timeout=float(timeout),
                )
            if mode == "imap":
                if not host or not username or password is None:
                    return "Error: host, username, and password are required for imap mode."
                return imap_operation_service(
                    host=host,
                    username=username,
                    password=password,
                    operation=operation or "list",
                    port=int(port) if port not in (None, "") else None,
                    use_ssl=_bool(use_ssl, True),
                    mailbox=mailbox,
                    search_criteria=search_criteria,
                    limit=int(limit),
                    uid=uid,
                    save_attachments_dir=save_attachments_dir,
                    mark_seen=_bool(mark_seen),
                    timeout=float(timeout),
                )
            if mode == "ftp_connect":
                if not host:
                    return "Error: host is required for ftp_connect mode."
                return ftp_connect_service(
                    host=host,
                    username=username or "anonymous",
                    password=password or "",
                    port=int(port) if port not in (None, "") else 21,
                    use_tls=_bool(starttls),
                    timeout=float(timeout),
                )
            if mode == "ftp":
                if not connection_id or not operation:
                    return "Error: connection_id and operation are required for ftp mode."
                return ftp_operation_service(
                    connection_id=connection_id,
                    operation=operation,
                    remote_path=remote_path,
                    local_path=destination,
                    new_remote_path=new_remote_path,
                    overwrite=_bool(overwrite),
                )
            if mode == "ftp_disconnect":
                if not connection_id:
                    return "Error: connection_id is required for ftp_disconnect mode."
                return ftp_disconnect_service(connection_id)
            if mode == "webhook":
                if not platform or not url or message is None:
                    return "Error: platform, url, and message are required for webhook mode."
                return webhook_send_service(
                    platform=platform,
                    webhook_url=url,
                    message=message,
                    msg_type=msg_type,
                    title=title or "",
                    at_mobiles=at_mobiles,
                    at_user_ids=at_user_ids,
                    secret=secret or "",
                    timeout=float(timeout),
                )
            return f"Error: Unknown Net mode: {mode}"
        except Exception as exc:
            return f"Error: Net operation failed: {exc}"

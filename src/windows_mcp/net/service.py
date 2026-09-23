"""Network service for HTTP, mail, FTP, and group-chat webhooks.

Credentials are accepted only as function arguments and are never logged or persisted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import imaplib
import json
import mimetypes
import os
import re
import shlex
import smtplib
import ssl
import threading
import time
import uuid
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from ftplib import FTP, FTP_TLS, error_perm
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

_FTP_SESSIONS: dict[str, FTP] = {}
_FTP_LOCK = threading.Lock()
_SENSITIVE_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie"}


def _is_true(value: Any, default: bool = False) -> bool:
    """Normalize booleans passed by MCP clients as strings."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_dict(value: Any, name: str) -> dict[str, Any]:
    """Return a mapping from a mapping or JSON object string."""
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError(f"{name} must be a JSON object")
        return parsed
    raise ValueError(f"{name} must be an object")


def _as_list(value: Any, name: str) -> list[Any]:
    """Return a list from a list, JSON array string, or scalar."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise ValueError(f"{name} must be a JSON array")
            return parsed
        return [part.strip() for part in re.split(r"[;,]", text) if part.strip()]
    return [value]


def _require_http_url(url: str) -> None:
    """Validate an HTTP or HTTPS URL."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be an absolute http:// or https:// URL")


def _redact_headers(headers: Any) -> dict[str, str]:
    """Copy response headers while redacting credential-bearing values."""
    if not headers:
        return {}
    return {
        str(key): "[redacted]" if str(key).lower() in _SENSITIVE_HEADERS else str(value)
        for key, value in headers.items()
    }


def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, Any] | str | None = None,
    params: dict[str, Any] | str | None = None,
    json_payload: Any = None,
    form_data: dict[str, Any] | str | None = None,
    raw_body: str | bytes | None = None,
    timeout: float = 30,
    allow_redirects: bool = True,
    verify: bool = True,
    max_text_length: int = 200_000,
) -> str:
    """Perform an arbitrary HTTP request with requests and return a structured summary.

    Header values are not echoed in the result. Use JSON/form/raw body at most once.
    """
    try:
        _require_http_url(url)
        request_headers = _as_dict(headers, "headers")
        query = _as_dict(params, "params")
        form = _as_dict(form_data, "form_data")
        body_kinds = sum(
            item is not None for item in (json_payload, form_data if form else None, raw_body)
        )
        if body_kinds > 1:
            return "Error: Provide only one of json_payload, form_data, or raw_body."

        request_kwargs: dict[str, Any] = {
            "headers": request_headers,
            "params": query,
            "timeout": float(timeout),
            "allow_redirects": _is_true(allow_redirects, True),
            "verify": _is_true(verify, True),
        }
        if json_payload is not None:
            if isinstance(json_payload, str):
                try:
                    request_kwargs["json"] = json.loads(json_payload)
                except json.JSONDecodeError:
                    return "Error: json_payload is a string but is not valid JSON."
            else:
                request_kwargs["json"] = json_payload
        elif form:
            request_kwargs["data"] = form
        elif raw_body is not None:
            request_kwargs["data"] = (
                raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body
            )

        response = requests.request(str(method or "GET").upper(), url, **request_kwargs)
        limit = max(0, int(max_text_length))
        text = response.text
        truncated = len(text) > limit
        result = {
            "status_code": response.status_code,
            "reason": response.reason,
            "url": response.url,
            "elapsed_seconds": round(response.elapsed.total_seconds(), 4),
            "headers": _redact_headers(response.headers),
            "text": text[:limit],
            "text_truncated": truncated,
            "content_length_bytes": len(response.content),
        }
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except requests.RequestException as exc:
        return f"Error: HTTP request failed: {exc}"
    except Exception as exc:
        return f"Error: HTTP request failed: {exc}"


def http_download(
    url: str,
    destination: str,
    overwrite: bool = False,
    resume: bool = True,
    expected_size: int | None = None,
    chunk_size: int = 64 * 1024,
    timeout: float = 60,
    headers: dict[str, Any] | str | None = None,
    allow_redirects: bool = True,
) -> str:
    """Download a URL to an explicit local path with optional resume and size validation."""
    part_path: Path | None = None
    try:
        _require_http_url(url)
        destination_path = Path(destination).expanduser().resolve()
        if destination_path.exists() and destination_path.is_dir():
            return f"Error: Destination is a directory: {destination_path}"
        if destination_path.exists() and not _is_true(overwrite):
            return (
                f"Error: Destination already exists: {destination_path}. "
                "Set overwrite=True to replace it."
            )

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        part_path = Path(f"{destination_path}.part")
        expected = int(expected_size) if expected_size not in (None, "") else None
        if expected is not None and expected < 0:
            return "Error: expected_size must be non-negative."
        if part_path.exists() and part_path.is_dir():
            return f"Error: Partial path is a directory: {part_path}"

        existing = 0
        request_headers = _as_dict(headers, "headers")
        if part_path.exists() and _is_true(resume, True):
            existing = part_path.stat().st_size
        elif part_path.exists():
            part_path.unlink()
        if expected is not None and existing > expected:
            return f"Error: Existing partial file is larger than expected_size ({expected})."

        if existing:
            request_headers["Range"] = f"bytes={existing}-"
        response = requests.get(
            url,
            headers=request_headers,
            stream=True,
            timeout=float(timeout),
            allow_redirects=_is_true(allow_redirects, True),
        )

        if response.status_code == 416:
            if expected is not None and part_path.exists() and part_path.stat().st_size == expected:
                os.replace(part_path, destination_path)
                return f"Downloaded: {destination_path} ({expected:,} bytes, already complete)"
            response.close()
            return f"Error: Download range rejected (HTTP 416). Partial size is {existing:,} bytes."

        response.raise_for_status()
        append = response.status_code == 206 and existing > 0
        if not append:
            existing = 0
            if part_path.exists():
                part_path.unlink()

        total: int | None = None
        content_range = response.headers.get("Content-Range", "")
        if "/" in content_range:
            candidate = content_range.rsplit("/", 1)[1]
            if candidate.isdigit():
                total = int(candidate)
        if total is None and response.headers.get("Content-Length", "").isdigit():
            total = existing + int(response.headers["Content-Length"])

        mode = "ab" if append else "wb"
        downloaded = existing
        with part_path.open(mode) as handle:
            for chunk in response.iter_content(chunk_size=max(1024, int(chunk_size))):
                if chunk:
                    handle.write(chunk)
                    downloaded += len(chunk)
        response.close()

        if expected is not None and downloaded != expected:
            return (
                f"Error: Size mismatch for {url}. Expected {expected:,} bytes, "
                f"received {downloaded:,} bytes. Partial file kept at {part_path}."
            )
        if total is not None and downloaded != total:
            return (
                f"Error: Incomplete download. Expected {total:,} bytes, "
                f"received {downloaded:,} bytes. Partial file kept at {part_path}."
            )

        os.replace(part_path, destination_path)
        percent = f" ({downloaded / total:.1%})" if total else ""
        return (
            f"Downloaded: {destination_path}\n"
            f"Bytes: {downloaded:,}{percent}\n"
            f"Resumed from: {existing:,} bytes"
        )
    except requests.RequestException as exc:
        return f"Error: Download failed: {exc}"
    except Exception as exc:
        return f"Error: Download failed: {exc}"


def _addresses(value: Any, name: str) -> list[str]:
    """Normalize one or more email addresses."""
    result: list[str] = []
    for item in _as_list(value, name):
        text = str(item).strip()
        if text:
            _, address = parseaddr(text)
            if not address or "@" not in address:
                raise ValueError(f"Invalid {name} address: {text}")
            result.append(text)
    return result


def _attachment_paths(value: Any) -> list[Path]:
    """Normalize attachment paths and verify they are files."""
    paths: list[Path] = []
    for item in _as_list(value, "attachments"):
        path = Path(str(item)).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise ValueError(f"Attachment not found or not a file: {path}")
        paths.append(path)
    return paths


def smtp_send(
    host: str,
    sender: str,
    recipients: str | list[str],
    subject: str,
    body_text: str = "",
    body_html: str | None = None,
    cc: str | list[str] | None = None,
    bcc: str | list[str] | None = None,
    attachments: str | list[str] | None = None,
    username: str | None = None,
    password: str | None = None,
    port: int | None = None,
    use_ssl: bool = True,
    starttls: bool = False,
    timeout: float = 30,
) -> str:
    """Send an email through SMTP/SSL; credentials are never returned or logged."""
    try:
        to_addresses = _addresses(recipients, "recipients")
        cc_addresses = _addresses(cc, "cc")
        bcc_addresses = _addresses(bcc, "bcc")
        if not to_addresses:
            return "Error: At least one recipient is required."
        _, sender_address = parseaddr(sender)
        if not sender_address or "@" not in sender_address:
            return "Error: sender must be a valid email address."

        message = EmailMessage()
        message["From"] = sender
        message["To"] = ", ".join(to_addresses)
        if cc_addresses:
            message["Cc"] = ", ".join(cc_addresses)
        message["Subject"] = subject
        if body_html:
            message.set_content(body_text or "This message contains HTML.")
            message.add_alternative(body_html, subtype="html")
        else:
            message.set_content(body_text)

        for path in _attachment_paths(attachments):
            mime, _ = mimetypes.guess_type(path.name)
            maintype, subtype = (mime or "application/octet-stream").split("/", 1)
            message.add_attachment(
                path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name
            )

        ssl_enabled = _is_true(use_ssl, True)
        starttls_enabled = _is_true(starttls)
        if port is None:
            port = 465 if ssl_enabled else (587 if starttls_enabled else 25)
        context = ssl.create_default_context()
        if ssl_enabled:
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                host, int(port), timeout=float(timeout), context=context
            )
        else:
            smtp = smtplib.SMTP(host, int(port), timeout=float(timeout))
        try:
            smtp.ehlo()
            if starttls_enabled and not ssl_enabled:
                smtp.starttls(context=context)
                smtp.ehlo()
            if username:
                smtp.login(username, password or "")
            smtp.send_message(message, to_addrs=to_addresses + cc_addresses + bcc_addresses)
        finally:
            try:
                smtp.quit()
            except Exception:
                smtp.close()
        return (
            f"Email sent to {len(to_addresses)} To, {len(cc_addresses)} Cc, "
            f"{len(bcc_addresses)} Bcc recipient(s).\nMessage bytes: {len(message.as_bytes()):,}"
        )
    except Exception as exc:
        return f"Error: SMTP send failed: {exc}"


def _uid_tokens(data: Any) -> list[str]:
    """Extract UID tokens from an imaplib result."""
    tokens: list[str] = []
    for item in data or []:
        if isinstance(item, bytes):
            tokens.extend(part.decode("ascii", errors="ignore") for part in item.split())
        elif isinstance(item, str):
            tokens.extend(item.split())
    return tokens


def _fetch_bytes(data: Any) -> bytes:
    """Extract message bytes from an imaplib FETCH result."""
    for item in data or []:
        if isinstance(item, tuple):
            for part in item:
                if isinstance(part, bytes) and (b"\n" in part or len(part) > 32):
                    return part
        elif isinstance(item, bytes) and b"\n" in item:
            return item
    return b""


def _quote_mailbox(mailbox: str) -> str:
    """Quote an IMAP mailbox name."""
    escaped = mailbox.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _message_summary(raw: bytes, uid: str) -> dict[str, Any]:
    """Build a non-destructive email summary from message headers."""
    message = BytesParser(policy=policy.default).parsebytes(raw)
    return {
        "uid": uid,
        "from": str(message.get("From", "")),
        "to": str(message.get("To", "")),
        "subject": str(message.get("Subject", "")),
        "date": str(message.get("Date", "")),
        "message_id": str(message.get("Message-ID", "")),
        "attachments": [
            part.get_filename()
            for part in message.walk()
            if part.get_content_disposition() == "attachment" and part.get_filename()
        ],
    }


def _decode_email_part(part: Any) -> str:
    """Return a text part as a string."""
    content = part.get_content()
    if isinstance(content, bytes):
        return content.decode(part.get_content_charset() or "utf-8", errors="replace")
    return str(content)


def _safe_attachment_name(name: str | None, index: int, content_type: str) -> str:
    """Return a basename safe for writing inside the selected attachment directory."""
    candidate = Path(name or f"attachment-{index}").name
    if not candidate or candidate in {".", ".."}:
        candidate = f"attachment-{index}"
    if "." not in candidate and "/" in content_type:
        subtype = content_type.split("/", 1)[1].split(";", 1)[0]
        if subtype and subtype != "octet-stream":
            candidate = f"{candidate}.{subtype}"
    return candidate


def _unique_path(directory: Path, filename: str) -> Path:
    """Choose a non-existing path without allowing directory traversal."""
    candidate = directory / Path(filename).name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    counter = 1
    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def imap_operation(
    host: str,
    username: str,
    password: str,
    operation: str = "list",
    port: int | None = None,
    use_ssl: bool = True,
    mailbox: str = "INBOX",
    search_criteria: str | list[str] | None = None,
    limit: int = 20,
    uid: str | int | None = None,
    save_attachments_dir: str | None = None,
    mark_seen: bool = False,
    timeout: float = 30,
    max_body_length: int = 200_000,
) -> str:
    """List, fetch, or mark IMAP messages; credentials stay in memory for this call only."""
    client: imaplib.IMAP4 | None = None
    try:
        ssl_enabled = _is_true(use_ssl, True)
        actual_port = int(port or (993 if ssl_enabled else 143))
        context = ssl.create_default_context()
        if ssl_enabled:
            client = imaplib.IMAP4_SSL(
                host, actual_port, ssl_context=context, timeout=float(timeout)
            )
        else:
            client = imaplib.IMAP4(host, actual_port, timeout=float(timeout))
        client.login(username, password)
        status, _ = client.select(_quote_mailbox(mailbox), readonly=operation.lower() == "list")
        if status != "OK":
            return f"Error: Unable to select mailbox {mailbox!r}."

        selected_operation = str(operation or "list").lower()
        if selected_operation in {"list", "search"}:
            criteria = search_criteria or "ALL"
            if isinstance(criteria, str):
                criteria_args = shlex.split(criteria)
            else:
                criteria_args = [str(item) for item in criteria]
            status, data = client.uid("search", None, *criteria_args)
            if status != "OK":
                return f"Error: IMAP search failed: {status}"
            uids = _uid_tokens(data)
            selected = uids[-max(1, int(limit)) :]
            rows: list[dict[str, Any]] = []
            for item_uid in selected:
                status, message_data = client.uid(
                    "fetch",
                    item_uid,
                    "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])",
                )
                if status == "OK":
                    rows.append(_message_summary(_fetch_bytes(message_data), item_uid))
            return json.dumps(
                {"mailbox": mailbox, "criteria": criteria, "count": len(rows), "messages": rows},
                ensure_ascii=False,
                indent=2,
            )

        if selected_operation == "fetch":
            if uid in (None, ""):
                return "Error: uid is required for the fetch operation."
            fetch_query = "(RFC822)" if _is_true(mark_seen) else "(BODY.PEEK[])"
            status, data = client.uid("fetch", str(uid), fetch_query)
            if status != "OK":
                return f"Error: IMAP fetch failed: {status}"
            raw = _fetch_bytes(data)
            if not raw:
                return f"Error: No message data returned for UID {uid}."
            message = BytesParser(policy=policy.default).parsebytes(raw)
            body_text_parts: list[str] = []
            body_html_parts: list[str] = []
            attachments: list[dict[str, Any]] = []
            attachment_dir = (
                Path(save_attachments_dir).expanduser().resolve() if save_attachments_dir else None
            )
            if attachment_dir:
                attachment_dir.mkdir(parents=True, exist_ok=True)
            for index, part in enumerate(message.walk(), start=1):
                content_type = part.get_content_type()
                disposition = part.get_content_disposition()
                if disposition == "attachment" or (disposition == "inline" and part.get_filename()):
                    payload = part.get_payload(decode=True) or b""
                    info: dict[str, Any] = {
                        "filename": part.get_filename(),
                        "content_type": content_type,
                        "size": len(payload),
                        "saved_to": None,
                    }
                    if attachment_dir:
                        target = _unique_path(
                            attachment_dir,
                            _safe_attachment_name(part.get_filename(), index, content_type),
                        )
                        target.write_bytes(payload)
                        info["saved_to"] = str(target)
                    attachments.append(info)
                elif content_type == "text/plain":
                    body_text_parts.append(_decode_email_part(part))
                elif content_type == "text/html":
                    body_html_parts.append(_decode_email_part(part))
            body_text = "\n".join(body_text_parts)
            body_html = "\n".join(body_html_parts)
            body_limit = max(0, int(max_body_length))
            return json.dumps(
                {
                    "uid": str(uid),
                    "from": str(message.get("From", "")),
                    "to": str(message.get("To", "")),
                    "cc": str(message.get("Cc", "")),
                    "subject": str(message.get("Subject", "")),
                    "date": str(message.get("Date", "")),
                    "body_text": body_text[:body_limit],
                    "body_text_truncated": len(body_text) > body_limit,
                    "body_html": body_html[:body_limit],
                    "body_html_truncated": len(body_html) > body_limit,
                    "attachments": attachments,
                },
                ensure_ascii=False,
                indent=2,
            )

        if selected_operation in {"mark_read", "mark_seen"}:
            if uid in (None, ""):
                return "Error: uid is required for mark_read."
            uids = [part for part in re.split(r"[,\s]+", str(uid)) if part]
            for item_uid in uids:
                status, _ = client.uid("store", item_uid, "+FLAGS", "(\\Seen)")
                if status != "OK":
                    return f"Error: Failed to mark UID {item_uid} as read."
            return f"Marked {len(uids)} message(s) as read."

        return "Error: operation must be list, search, fetch, or mark_read."
    except Exception as exc:
        return f"Error: IMAP operation failed: {exc}"
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass


def ftp_connect(
    host: str,
    username: str = "anonymous",
    password: str = "",
    port: int = 21,
    use_tls: bool = False,
    timeout: float = 30,
) -> str:
    """Open an FTP/FTPS session and return a connection_id; credentials are not persisted."""
    ftp: FTP | None = None
    try:
        if _is_true(use_tls):
            ftp = FTP_TLS(context=ssl.create_default_context())
        else:
            ftp = FTP()
        ftp.connect(host, int(port), timeout=float(timeout))
        ftp.login(username, password)
        if isinstance(ftp, FTP_TLS):
            ftp.prot_p()
        connection_id = uuid.uuid4().hex
        with _FTP_LOCK:
            _FTP_SESSIONS[connection_id] = ftp
        return (
            f"FTP connected: {host}:{port}\n"
            f"Connection ID: {connection_id}\n"
            f"Welcome: {str(getattr(ftp, 'welcome', '') or '')[:200]}"
        )
    except Exception as exc:
        if ftp is not None:
            try:
                ftp.close()
            except Exception:
                pass
        return f"Error: FTP connect failed: {exc}"


def ftp_disconnect(connection_id: str) -> str:
    """Close an FTP session opened by ftp_connect."""
    with _FTP_LOCK:
        ftp = _FTP_SESSIONS.pop(connection_id, None)
    if ftp is None:
        return f"Error: Unknown FTP connection_id: {connection_id}"
    try:
        ftp.quit()
    except Exception:
        try:
            ftp.close()
        except Exception:
            pass
    return f"FTP disconnected: {connection_id}"


def _ftp_session(connection_id: str) -> FTP:
    """Return an active FTP session or raise a clear error."""
    with _FTP_LOCK:
        ftp = _FTP_SESSIONS.get(connection_id)
    if ftp is None:
        raise ValueError(f"Unknown FTP connection_id: {connection_id}")
    return ftp


def ftp_operation(
    connection_id: str,
    operation: str,
    remote_path: str | None = None,
    local_path: str | None = None,
    new_remote_path: str | None = None,
    overwrite: bool = False,
    max_entries: int = 1000,
) -> str:
    """Execute an FTP operation on an existing connection."""
    try:
        ftp = _ftp_session(connection_id)
        selected = str(operation or "").lower()
        with _FTP_LOCK:
            if selected == "list":
                target = remote_path or "."
                lines: list[str] = []
                try:
                    for name, facts in ftp.mlsd(target):
                        lines.append(f"{name}\t{facts}")
                except error_perm, AttributeError:
                    ftp.retrlines(f"LIST {target}".strip(), lines.append)
                limit = max(1, int(max_entries))
                return (
                    "FTP listing:\n"
                    + "\n".join(lines[:limit])
                    + (f"\n... truncated ({len(lines)} total)" if len(lines) > limit else "")
                )

            if selected == "upload":
                if not local_path or not remote_path:
                    return "Error: local_path and remote_path are required for upload."
                source = Path(local_path).expanduser().resolve()
                if not source.exists() or not source.is_file():
                    return f"Error: Local file not found: {source}"
                with source.open("rb") as handle:
                    ftp.storbinary(f"STOR {remote_path}", handle)
                return f"FTP uploaded: {source} -> {remote_path} ({source.stat().st_size:,} bytes)"

            if selected == "download":
                if not remote_path or not local_path:
                    return "Error: remote_path and local_path are required for download."
                destination = Path(local_path).expanduser().resolve()
                if destination.exists() and not _is_true(overwrite):
                    return f"Error: Destination exists: {destination}. Set overwrite=True."
                if destination.exists() and destination.is_dir():
                    return f"Error: Destination is a directory: {destination}"
                destination.parent.mkdir(parents=True, exist_ok=True)
                part = Path(f"{destination}.part")
                with part.open("wb") as handle:
                    ftp.retrbinary(f"RETR {remote_path}", handle.write)
                os.replace(part, destination)
                return f"FTP downloaded: {remote_path} -> {destination} ({destination.stat().st_size:,} bytes)"

            if selected == "delete":
                if not remote_path:
                    return "Error: remote_path is required for delete."
                ftp.delete(remote_path)
                return f"FTP deleted file: {remote_path}"

            if selected == "rename":
                if not remote_path or not new_remote_path:
                    return "Error: remote_path and new_remote_path are required for rename."
                ftp.rename(remote_path, new_remote_path)
                return f"FTP renamed: {remote_path} -> {new_remote_path}"

            if selected == "mkdir":
                if not remote_path:
                    return "Error: remote_path is required for mkdir."
                ftp.mkd(remote_path)
                return f"FTP directory created: {remote_path}"

            if selected == "rmdir":
                if not remote_path:
                    return "Error: remote_path is required for rmdir."
                ftp.rmd(remote_path)
                return f"FTP directory deleted: {remote_path}"

            if selected == "cwd":
                target = remote_path or "/"
                ftp.cwd(target)
                return f"FTP current directory: {ftp.pwd()}"

            if selected == "pwd":
                return f"FTP current directory: {ftp.pwd()}"

        return "Error: operation must be list, upload, download, delete, rename, mkdir, rmdir, cwd, or pwd."
    except Exception as exc:
        return f"Error: FTP operation failed: {exc}"


def webhook_send(
    platform: str,
    webhook_url: str,
    message: str,
    msg_type: str = "text",
    title: str = "",
    at_mobiles: str | list[str] | None = None,
    at_user_ids: str | list[str] | None = None,
    secret: str = "",
    timeout: float = 15,
) -> str:
    """Send a WeCom, DingTalk, or Feishu webhook message and parse vendor error codes."""
    try:
        _require_http_url(webhook_url)
        selected = str(platform or "").strip().lower()
        aliases = {
            "wecom": "wecom",
            "wechat_work": "wecom",
            "qywx": "wecom",
            "dingtalk": "dingtalk",
            "ding": "dingtalk",
            "feishu": "feishu",
            "lark": "feishu",
        }
        selected = aliases.get(selected, selected)
        if selected not in {"wecom", "dingtalk", "feishu"}:
            return "Error: platform must be wecom, dingtalk, or feishu."
        selected_type = str(msg_type or "text").lower()
        if selected_type not in {"text", "markdown"}:
            return "Error: msg_type must be text or markdown."
        mobiles = [str(item) for item in _as_list(at_mobiles, "at_mobiles")]
        user_ids = [str(item) for item in _as_list(at_user_ids, "at_user_ids")]
        request_params: dict[str, Any] = {}
        payload: dict[str, Any]

        if selected == "wecom":
            if selected_type == "markdown":
                mention_text = "".join(f" <@{item}>" for item in user_ids if item != "all")
                payload = {
                    "msgtype": "markdown",
                    "markdown": {"content": f"{message}{mention_text}"},
                }
            else:
                payload = {
                    "msgtype": "text",
                    "text": {
                        "content": message,
                        "mentioned_list": user_ids,
                        "mentioned_mobile_list": mobiles,
                    },
                }
        elif selected == "dingtalk":
            if secret:
                timestamp = str(int(time.time() * 1000))
                string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
                digest = hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
                request_params = {
                    "timestamp": timestamp,
                    "sign": base64.b64encode(digest).decode("utf-8"),
                }
            at_payload = {"atMobiles": mobiles, "atUserIds": user_ids, "isAtAll": "all" in user_ids}
            if selected_type == "markdown":
                payload = {
                    "msgtype": "markdown",
                    "markdown": {"title": title or "Notification", "text": message},
                    "at": at_payload,
                }
            else:
                payload = {
                    "msgtype": "text",
                    "text": {"content": message},
                    "at": at_payload,
                }
        else:
            mention_text = message
            for item in user_ids:
                mention_text += f' <at id="{item}"></at>'
            if selected_type == "markdown":
                payload = {
                    "msg_type": "interactive",
                    "card": {
                        "header": {
                            "title": {"tag": "plain_text", "content": title or "Notification"}
                        },
                        "elements": [
                            {"tag": "div", "text": {"tag": "lark_md", "content": mention_text}}
                        ],
                    },
                }
            else:
                payload = {"msg_type": "text", "content": {"text": mention_text}}

        response = requests.post(
            webhook_url, params=request_params, json=payload, timeout=float(timeout)
        )
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text[:2000]}
        error_code = body.get("errcode", body.get("code", body.get("StatusCode", 0)))
        error_message = body.get(
            "errmsg",
            body.get("msg", body.get("StatusMessage", body.get("message", ""))),
        )
        if response.status_code >= 400 or (error_code not in (0, "0", None)):
            return (
                f"Error: Webhook rejected (HTTP {response.status_code}, code {error_code}): "
                f"{error_message}"
            )
        return json.dumps(
            {"platform": selected, "http_status": response.status_code, "response": body},
            ensure_ascii=False,
            indent=2,
        )
    except requests.RequestException:
        return "Error: Webhook request failed."
    except Exception as exc:
        return f"Error: Webhook send failed: {type(exc).__name__}."

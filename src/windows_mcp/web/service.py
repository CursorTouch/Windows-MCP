"""Playwright-backed browser automation service.

The service keeps synchronous Playwright objects on one worker thread. MCP
tool calls may be dispatched from multiple asyncio executor threads, so this
serialization keeps browser state deterministic and makes CDP reuse safe.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from playwright.sync_api import Locator, Page, Playwright, sync_playwright

DEFAULT_CDP_ENDPOINT = "http://127.0.0.1:9222"
_MAX_TEXT = 4000
_MAX_ATTR = 120


@dataclass(slots=True)
class _ElementRef:
    """A cached snapshot reference and its stable Playwright locator."""

    ref: int
    token: str
    locator: Locator
    role: str
    name: str
    tag: str


_JS_SNAPSHOT = r"""
() => {
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const visible = (el) => {
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 &&
      rect.bottom >= 0 && rect.right >= 0 &&
      rect.top <= innerHeight && rect.left <= innerWidth;
  };
  const roleFor = (el) => {
    const explicit = clean(el.getAttribute("role")).toLowerCase();
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    const type = clean(el.getAttribute("type")).toLowerCase();
    if (tag === "a" && el.hasAttribute("href")) return "link";
    if (tag === "button" || tag === "summary") return "button";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "input") {
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (["button", "submit", "reset", "image"].includes(type)) return "button";
      return "textbox";
    }
    if (el.isContentEditable) return "textbox";
    return tag;
  };
  const labelFor = (el) => {
    let label = clean(el.getAttribute("aria-label"));
    const labelledBy = clean(el.getAttribute("aria-labelledby"));
    if (!label && labelledBy) {
      label = clean(labelledBy.split(/\s+/).map((id) => {
        const node = document.getElementById(id);
        return node ? node.innerText || node.textContent : "";
      }).join(" "));
    }
    if (!label && el.labels && el.labels.length) {
      label = clean(Array.from(el.labels).map((node) => node.innerText || node.textContent).join(" "));
    }
    if (!label) label = clean(el.getAttribute("placeholder"));
    if (!label) label = clean(el.getAttribute("title"));
    if (!label) label = clean(el.getAttribute("alt"));
    if (!label) {
      const text = clean(el.innerText || el.textContent);
      if (text) label = text.slice(0, 160);
    }
    if (!label) label = clean(el.getAttribute("name"));
    return label.slice(0, 160);
  };
  const valueFor = (el, role) => {
    const type = clean(el.getAttribute("type")).toLowerCase();
    if (type === "checkbox" || type === "radio") return el.checked ? "checked" : "unchecked";
    if (el.isContentEditable) return clean(el.innerText || el.textContent).slice(0, 160);
    if (role === "combobox" && el.tagName.toLowerCase() === "select") return clean(el.value).slice(0, 160);
    if (["textbox", "combobox"].includes(role) && "value" in el) return clean(el.value).slice(0, 160);
    return undefined;
  };
  const old = Array.from(document.querySelectorAll("[data-windows-mcp-ref]"));
  old.forEach((node) => node.removeAttribute("data-windows-mcp-ref"));
  const selector = [
    "input:not([type=hidden])", "textarea", "select", "button", "a[href]",
    "[role=button]", "[role=link]", "[role=textbox]", "[role=combobox]",
    "[contenteditable=true]", "[tabindex]:not([tabindex='-1'])", "summary", "[onclick]"
  ].join(",");
  const rows = [];
  const seen = new Set();
  for (const el of Array.from(document.querySelectorAll(selector))) {
    if (seen.has(el) || !visible(el)) continue;
    seen.add(el);
    const role = roleFor(el);
    const label = labelFor(el);
    const value = valueFor(el, role);
    const ref = rows.length + 1;
    const token = "w" + Date.now().toString(36) + "-" + ref + "-" +
      Math.random().toString(36).slice(2, 8);
    el.setAttribute("data-windows-mcp-ref", token);
    rows.push({
      ref,
      token,
      role,
      label,
      value,
      tag: el.tagName.toLowerCase(),
      disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true"),
      href: el.tagName.toLowerCase() === "a" ? clean(el.getAttribute("href")) : "",
    });
  }
  return {url: location.href, title: document.title, rows};
}
"""


def _as_list(value: Any, name: str) -> list[Any] | None:
    """Coerce a JSON list or JSON-list string into a Python list."""

    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name} must be a list or a JSON list string") from exc
        if isinstance(parsed, list):
            return parsed
    raise ValueError(f"{name} must be a list or a JSON list string")


def _as_dict(value: Any, name: str) -> dict[str, Any]:
    """Coerce a JSON object or JSON-object string into a dictionary."""

    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name} must be an object or a JSON object string") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{name} must be an object or a JSON object string")


def _as_dict_list(value: Any, name: str) -> list[dict[str, Any]]:
    """Coerce a JSON list of objects into cookie-like data."""

    if isinstance(value, dict):
        return [dict(value)]
    items = _as_list(value, name)
    if items is None:
        raise ValueError(f"{name} is required")
    if not all(isinstance(item, dict) for item in items):
        raise ValueError(f"{name} must contain only JSON objects")
    return [dict(item) for item in items]


def _as_string_list(value: Any, name: str) -> list[str] | None:
    """Coerce a string or list into a validated list of strings."""

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = value
        if isinstance(parsed, str):
            return [parsed]
        if isinstance(parsed, list):
            items = parsed
        else:
            raise ValueError(f"{name} must be a string or a JSON list")
    else:
        items = _as_list(value, name)
    if items is None:
        return None
    if not all(isinstance(item, str) for item in items):
        raise ValueError(f"{name} must contain only strings")
    return items


def _as_bool(value: Any, name: str) -> bool:
    """Coerce tool-serialized boolean values."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(f"{name} must be true or false")


def _as_int(value: Any, name: str, *, minimum: int | None = None) -> int:
    """Coerce an integer-like value and enforce an optional minimum."""

    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return parsed


def _parse_ref(value: Any) -> int:
    """Parse a ref in 1, string-number, or bracketed-string form."""

    text = str(value).strip()
    match = re.fullmatch(r"\[?(\d+)\]?", text)
    if not match:
        raise ValueError("ref must be a positive integer or a value like '[1]'")
    number = int(match.group(1))
    if number < 1:
        raise ValueError("ref must be a positive integer")
    return number


def _quote(value: Any) -> str:
    """Render a compact, escaped quoted value for snapshot output."""

    text = str(value if value is not None else "")
    text = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{text}"'


def _truncate(value: Any, limit: int = _MAX_TEXT) -> str:
    """Convert a value to text and cap its size."""

    text = value if isinstance(value, str) else str(value)
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


#: How long ``launch`` waits for a freshly navigated page to expose interactive
#: elements before handing control back to the caller.
_SETTLE_TIMEOUT_MS = 10_000


def _settle_page(page: "Page", *, timeout_ms: int = _SETTLE_TIMEOUT_MS) -> int:
    """Wait until a navigated page exposes at least one interactive element.

    ``domcontentloaded`` and ``load`` both fire before SPA shells and
    consent-gated homepages finish rendering their controls. Snapshotting that
    early yields an empty or partial element table, and the agent then maps the
    index it chose onto the wrong node. Poll until elements exist, then take one
    more reading so late controls are included. A page that genuinely has no
    interactive elements is still returned once the budget runs out.

    Returns the largest number of elements observed.
    """

    deadline = time.monotonic() + max(0, timeout_ms) / 1000
    best = 0
    while True:
        try:
            rows = page.evaluate(_JS_SNAPSHOT).get("rows", []) or []
            count = len(rows)
        except Exception:
            count = 0
        best = max(best, count)
        if count > 0:
            time.sleep(0.35)
            try:
                rows = page.evaluate(_JS_SNAPSHOT).get("rows", []) or []
                best = max(best, len(rows))
            except Exception:
                pass
            return best
        if time.monotonic() >= deadline:
            return best
        time.sleep(0.15)


class WebAutomationService:
    """Deterministic Playwright automation with Jev-style indexed refs."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="windows-mcp-web")
        self._playwright: Playwright | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Page | None = None
        self._endpoint = DEFAULT_CDP_ENDPOINT
        self._mode = "closed"
        self._refs: dict[int, _ElementRef] = {}
        self._network_events: list[dict[str, Any]] = []
        self._network_handlers: list[tuple[Page, str, Any]] = []
        self._executor_lock = Lock()
        atexit.register(self.shutdown)

    def _run(self, function: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a browser operation on the single Playwright owner thread."""

        with self._executor_lock:
            future = self._executor.submit(function, *args, **kwargs)
        return future.result()

    def shutdown(self) -> None:
        """Best-effort cleanup used by atexit and callers."""

        try:
            self._run(self._close_sync)
        except Exception:
            pass
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    def connect(self, endpoint: str = DEFAULT_CDP_ENDPOINT, timeout_ms: int = 10000) -> str:
        """Connect to an already-running Chrome instance over CDP."""

        return self._run(self._connect_sync, endpoint, timeout_ms)

    def launch(
        self,
        headless: bool | str = False,
        url: str | None = None,
        timeout_ms: int = 30000,
    ) -> str:
        """Launch a fresh Chromium instance and optionally navigate it."""

        return self._run(self._launch_sync, headless, url, timeout_ms)

    def close(self) -> str:
        """Close the current browser and release the Playwright driver."""

        return self._run(self._close_public_sync)

    def status(self, tab_index: int | str | None = None) -> str:
        """Return connection, current tab, and page information."""

        return self._run(self._status_sync, tab_index)

    def goto(self, url: str, timeout_ms: int | str = 30000) -> str:
        """Navigate the current tab to *url* and settle the page."""

        return self._run(self._goto_sync, url, timeout_ms)

    def snapshot(self, max_elements: int | str = 150) -> str:
        """Return a compact indexed table of visible interactive elements."""

        return self._run(self._snapshot_sync, max_elements)

    def snapshot_data(self, max_elements: int | str = 150) -> dict[str, Any]:
        """Return URL, title, page text, and structured visible elements."""

        return self._run(self._snapshot_data_sync, max_elements)

    def click(
        self,
        ref: int | str | None = None,
        selector: str | None = None,
        timeout_ms: int | str = 30000,
    ) -> str:
        """Click a snapshot ref or CSS selector."""

        return self._run(self._click_sync, ref, selector, timeout_ms)

    def type_text(
        self,
        value: str,
        ref: int | str | None = None,
        selector: str | None = None,
        clear: bool | str = True,
        append: bool | str = False,
        press_enter: bool | str = False,
        timeout_ms: int | str = 30000,
    ) -> str:
        """Type into a ref or selector with clear/append semantics."""

        return self._run(
            self._type_sync,
            value,
            ref,
            selector,
            clear,
            append,
            press_enter,
            timeout_ms,
        )

    def select(
        self,
        value: Any,
        text: str | None = None,
        ref: int | str | None = None,
        selector: str | None = None,
        timeout_ms: int | str = 30000,
    ) -> str:
        """Select one or more options in a native select control."""

        return self._run(self._select_sync, value, text, ref, selector, timeout_ms)

    def scroll(
        self,
        direction: str = "down",
        amount: int | str = 600,
        ref: int | str | None = None,
        selector: str | None = None,
        scroll_to: str | None = None,
        timeout_ms: int | str = 30000,
    ) -> str:
        """Scroll the page or bring a ref/selector into view."""

        return self._run(
            self._scroll_sync,
            direction,
            amount,
            ref,
            selector,
            scroll_to,
            timeout_ms,
        )

    def hover(
        self,
        ref: int | str | None = None,
        selector: str | None = None,
        timeout_ms: int | str = 30000,
    ) -> str:
        """Move the mouse over a ref or selector."""

        return self._run(self._hover_sync, ref, selector, timeout_ms)

    def press(
        self,
        key: str,
        ref: int | str | None = None,
        selector: str | None = None,
        timeout_ms: int | str = 30000,
    ) -> str:
        """Press a keyboard key, optionally after focusing a target."""

        return self._run(self._press_sync, key, ref, selector, timeout_ms)

    def evaluate(self, script: str) -> str:
        """Evaluate JavaScript in the current page."""

        return self._run(self._evaluate_sync, script)

    def cookies(self, action: str = "get", payload: Any = None) -> str:
        """Get, set, or clear browser cookies."""

        return self._run(self._cookies_sync, action, payload)

    def network(self, action: str = "get", limit: int | str = 100) -> str:
        """Start, stop, or retrieve captured XHR/fetch traffic."""

        return self._run(self._network_sync, action, limit)

    def wait(
        self,
        wait_for: str,
        selector: str | None = None,
        text: str | None = None,
        url: str | None = None,
        timeout_ms: int | str = 30000,
    ) -> str:
        """Wait for a selector, text, URL, network idle, or fixed duration."""

        return self._run(self._wait_sync, wait_for, selector, text, url, timeout_ms)

    def screenshot(
        self,
        path: str | None = None,
        full_page: bool | str = True,
        ref: int | str | None = None,
        selector: str | None = None,
        timeout_ms: int | str = 30000,
    ) -> str:
        """Capture a full-page, viewport, or element screenshot."""

        return self._run(self._screenshot_sync, path, full_page, ref, selector, timeout_ms)

    def extract(
        self,
        selector: str | None = None,
        selectors: list[str] | str | None = None,
        fields: list[str] | str | None = None,
        attributes: list[str] | str | None = None,
        max_elements: int | str = 50,
    ) -> str:
        """Extract text, values, attributes, links, HTML, or tables."""

        return self._run(
            self._extract_sync,
            selector,
            selectors,
            fields,
            attributes,
            max_elements,
        )

    def _ensure_playwright(self) -> None:
        if self._playwright is None:
            self._playwright = sync_playwright().start()

    def _connect_sync(self, endpoint: str, timeout_ms: Any) -> str:
        timeout = _as_int(timeout_ms, "timeout_ms", minimum=1)
        if not endpoint or not endpoint.strip():
            raise ValueError("endpoint must not be empty")
        endpoint = endpoint.strip()
        if (
            self._browser is not None
            and self._browser.is_connected()
            and endpoint == self._endpoint
        ):
            self._mode = "connect"
            return self._status_sync(None)
        self._close_sync()
        self._ensure_playwright()
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(endpoint, timeout=timeout)
        except Exception as exc:
            raise RuntimeError(
                f"Could not connect to Chrome CDP endpoint {endpoint!r}: {exc}. "
                "Start Chrome with --remote-debugging-port=9222 and allow the endpoint."
            ) from exc
        contexts = self._browser.contexts
        self._context = contexts[0] if contexts else self._browser.new_context()
        pages = [page for page in self._context.pages if not page.is_closed()]
        self._page = pages[-1] if pages else self._context.new_page()
        self._endpoint = endpoint
        self._mode = "connect"
        self._refs.clear()
        return self._status_sync(None)

    def _launch_sync(self, headless: Any, url: str | None, timeout_ms: Any) -> str:
        timeout = _as_int(timeout_ms, "timeout_ms", minimum=1)
        headless_value = _as_bool(headless, "headless")
        self._close_sync()
        self._ensure_playwright()
        try:
            self._browser = self._playwright.chromium.launch(headless=headless_value)
        except Exception as exc:
            raise RuntimeError(
                f"Could not launch Chromium: {exc}. Run 'playwright install chromium' first."
            ) from exc
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        self._endpoint = "launch"
        self._mode = "launch"
        self._refs.clear()
        if url:
            try:
                page = self._page
                page.goto(url, timeout=timeout, wait_until="load")
            except Exception as exc:
                raise RuntimeError(
                    f"Chromium launched but navigation to {url!r} failed: {exc}"
                ) from exc
            _settle_page(page, timeout_ms=min(timeout, _SETTLE_TIMEOUT_MS))
        return self._status_sync(None)

    def _close_sync(self) -> None:
        self._detach_network_sync()
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
        self._browser = None
        self._context = None
        self._page = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._playwright = None
        self._refs.clear()
        self._mode = "closed"

    def _close_public_sync(self) -> str:
        self._close_sync()
        return "Browser closed."

    def _select_page_sync(self, tab_index: Any = None) -> Page:
        if self._browser is None or not self._browser.is_connected():
            raise RuntimeError(
                "Browser is not connected. Use mode='connect' or mode='launch' first."
            )
        if self._context is None:
            contexts = self._browser.contexts
            self._context = contexts[0] if contexts else self._browser.new_context()
        pages = [page for page in self._context.pages if not page.is_closed()]
        if not pages:
            self._page = self._context.new_page()
            return self._page
        if tab_index is None:
            if self._page is None or self._page.is_closed() or self._page not in pages:
                self._page = pages[-1]
            return self._page
        index = _as_int(tab_index, "tab_index")
        try:
            self._page = pages[index]
        except IndexError as exc:
            raise ValueError(
                f"tab_index {index} is out of range; {len(pages)} tab(s) are open."
            ) from exc
        return self._page

    def _status_sync(self, tab_index: Any = None) -> str:
        connected = bool(self._browser is not None and self._browser.is_connected())
        pages: list[dict[str, Any]] = []
        current_index: int | None = None
        current_url = ""
        current_title = ""
        if connected:
            try:
                page = self._select_page_sync(tab_index)
                page_list = [item for item in self._context.pages if not item.is_closed()]
                current_index = page_list.index(page) if page in page_list else None
                current_url = page.url
                try:
                    current_title = page.title()
                except Exception:
                    current_title = ""
                for index, item in enumerate(page_list):
                    pages.append({"index": index, "url": item.url})
            except Exception as exc:
                return json.dumps(
                    {"connected": True, "error": str(exc)}, ensure_ascii=False, indent=2
                )
        return json.dumps(
            {
                "connected": connected,
                "mode": self._mode,
                "endpoint": self._endpoint,
                "current_tab": current_index,
                "url": current_url,
                "title": current_title,
                "tabs": pages,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _goto_sync(self, url: Any, timeout_ms: Any) -> str:
        page = self._select_page_sync(None)
        timeout = _as_int(timeout_ms, "timeout_ms", minimum=1)
        target = url if isinstance(url, str) else str(url)
        if not target.strip():
            raise ValueError("url must be a non-empty string")
        try:
            page.goto(target.strip(), timeout=timeout, wait_until="load")
        except Exception as exc:
            raise RuntimeError(f"Navigation to {target!r} failed: {exc}") from exc
        self._refs.clear()
        _settle_page(page, timeout_ms=min(timeout, _SETTLE_TIMEOUT_MS))
        return f"Navigated to {page.url}."

    def _snapshot_sync(self, max_elements: Any) -> str:
        page = self._select_page_sync(None)
        maximum = _as_int(max_elements, "max_elements", minimum=1)
        raw = page.evaluate(_JS_SNAPSHOT)
        rows = raw.get("rows", [])[:maximum]
        self._refs = {}
        for row in rows:
            ref = int(row["ref"])
            token = str(row["token"])
            self._refs[ref] = _ElementRef(
                ref=ref,
                token=token,
                locator=page.locator(f'[data-windows-mcp-ref="{token}"]'),
                role=str(row.get("role", "")),
                name=str(row.get("label", "")),
                tag=str(row.get("tag", "")),
            )
        lines = [f"url={raw.get('url', page.url)}", f"title={raw.get('title', '')}"]
        if not rows:
            lines.append("(no visible interactive elements)")
        for row in rows:
            line = f"[{row['ref']}] {str(row.get('role', '')):<10} {_quote(row.get('label', ''))}"
            if "value" in row and row.get("value") is not None:
                line += f" value={_quote(row['value'])}"
            if row.get("disabled"):
                line += " disabled=true"
            if row.get("href"):
                line += f" href={_quote(row['href'])}"
            lines.append(line)
        return "\n".join(lines)

    def _snapshot_data_sync(self, max_elements: Any) -> dict[str, Any]:
        page = self._select_page_sync(None)
        maximum = _as_int(max_elements, "max_elements", minimum=1)
        raw = page.evaluate(_JS_SNAPSHOT)
        rows = raw.get("rows", [])[:maximum]
        self._refs = {}
        elements: list[dict[str, Any]] = []
        for row in rows:
            ref = int(row["ref"])
            token = str(row["token"])
            locator = page.locator(f'[data-windows-mcp-ref="{token}"]')
            self._refs[ref] = _ElementRef(
                ref=ref,
                token=token,
                locator=locator,
                role=str(row.get("role", "")),
                name=str(row.get("label", "")),
                tag=str(row.get("tag", "")),
            )
            state: dict[str, Any] = {}
            try:
                state = locator.evaluate(
                    """el => {
                        const options = el.tagName.toLowerCase() === "select"
                          ? Array.from(el.options).map((option, index) => ({
                              index: index + 1,
                              label: (option.label || option.textContent || option.value || "").trim().slice(0, 160),
                              value: option.value,
                              disabled: Boolean(option.disabled || option.closest("optgroup[disabled]"))
                            }))
                          : [];
                        return {
                          checked: Boolean(el.checked),
                          selected: Boolean(el.selected),
                          expanded: el.getAttribute("aria-expanded"),
                          contenteditable: el.isContentEditable,
                          input_type: el.getAttribute("type") || "",
                          options
                        };
                    }"""
                )
            except Exception:
                state = {}
            element = {key: value for key, value in row.items() if key != "token"}
            for key in (
                "checked",
                "selected",
                "expanded",
                "contenteditable",
                "input_type",
                "options",
            ):
                if key in state:
                    element[key] = state[key]
            elements.append(element)
        try:
            text = page.locator("body").inner_text(timeout=1000)
        except Exception:
            try:
                text = page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                text = ""
        text = _truncate(text, 12000)
        try:
            scroll_y = page.evaluate("() => window.scrollY || 0")
        except Exception:
            scroll_y = 0
        fingerprint_data = {
            "url": raw.get("url", page.url),
            "title": raw.get("title", ""),
            "text": text,
            "scroll_y": scroll_y,
            "elements": elements,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return {
            **fingerprint_data,
            "fingerprint": fingerprint,
        }

    def _resolve_target_sync(
        self,
        page: Page,
        ref: Any,
        selector: str | None,
        timeout_ms: Any,
    ) -> tuple[Locator, str]:
        timeout = _as_int(timeout_ms, "timeout_ms", minimum=1)
        if ref is not None:
            number = _parse_ref(ref)
            record = self._refs.get(number)
            if record is None:
                raise ValueError(
                    f"Unknown or stale ref [{number}]. Run mode='snapshot' again before acting."
                )
            locator = record.locator
            target = f"ref [{number}]"
        elif selector:
            locator = page.locator(selector).first
            target = f"selector {selector!r}"
        else:
            raise ValueError("Provide ref (from mode='snapshot') or selector for this action.")
        try:
            locator.wait_for(state="attached", timeout=timeout)
        except Exception as exc:
            raise ValueError(
                f"Target {target} was not found or is no longer attached: {exc}"
            ) from exc
        return locator, target

    def _ensure_visible_sync(self, locator: Locator, target: str, timeout_ms: Any) -> None:
        timeout = _as_int(timeout_ms, "timeout_ms", minimum=1)
        try:
            locator.wait_for(state="visible", timeout=timeout)
        except Exception as exc:
            raise ValueError(f"Target {target} is not visible: {exc}") from exc
        try:
            if not locator.is_enabled():
                raise ValueError(f"Target {target} is visible but disabled.")
        except ValueError:
            raise
        except Exception:
            pass

    def _ensure_not_obscured_sync(self, locator: Locator, target: str) -> None:
        box = locator.bounding_box()
        if not box:
            raise ValueError(f"Target {target} has no clickable bounding box.")
        point = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2}
        result = locator.evaluate(
            """(el, p) => {
                const hit = document.elementFromPoint(p.x, p.y);
                if (!hit) return {covered: true, by: "none", text: ""};
                const relates = hit === el || el.contains(hit) || hit.contains(el);
                return {
                  covered: !relates,
                  by: hit.tagName ? hit.tagName.toLowerCase() : "unknown",
                  text: (hit.innerText || hit.textContent || "").trim().slice(0, 80)
                };
            }""",
            point,
        )
        if result.get("covered"):
            raise RuntimeError(
                f"Target {target} is covered by {result.get('by', 'another element')} "
                f"{result.get('text', '')!r}."
            )

    def _read_value_sync(self, locator: Locator) -> str:
        try:
            return _truncate(locator.input_value(), 300)
        except Exception:
            try:
                return _truncate(locator.text_content(), 300)
            except Exception:
                return ""

    def _validation_sync(
        self,
        page: Page,
        locator: Locator | None,
        before_url: str,
        target: str,
    ) -> str:
        try:
            page.wait_for_timeout(50)
        except Exception:
            pass
        parts: list[str] = []
        if page.url != before_url:
            parts.append(f"navigated to {page.url}")
        else:
            parts.append("no navigation")
        if locator is not None:
            try:
                if locator.count():
                    parts.append(f"element_visible={locator.is_visible()}")
                else:
                    parts.append("element_detached_after_action=true")
            except Exception:
                parts.append("element_state=unknown")
        return f"{target}; validation: {', '.join(parts)}"

    def _click_sync(self, ref: Any, selector: str | None, timeout_ms: Any) -> str:
        page = self._select_page_sync(None)
        locator, target = self._resolve_target_sync(page, ref, selector, timeout_ms)
        self._ensure_visible_sync(locator, target, timeout_ms)
        self._ensure_not_obscured_sync(locator, target)
        before_url = page.url
        try:
            locator.click(timeout=_as_int(timeout_ms, "timeout_ms", minimum=1))
        except Exception as exc:
            raise RuntimeError(f"Click failed for {target}: {exc}") from exc
        return f"Clicked {target}; " + self._validation_sync(page, locator, before_url, target)

    def _type_sync(
        self,
        value: str,
        ref: Any,
        selector: str | None,
        clear: Any,
        append: Any,
        press_enter: Any,
        timeout_ms: Any,
    ) -> str:
        if value is None:
            raise ValueError("value is required for mode='type'.")
        page = self._select_page_sync(None)
        locator, target = self._resolve_target_sync(page, ref, selector, timeout_ms)
        self._ensure_visible_sync(locator, target, timeout_ms)
        clear_value = _as_bool(clear, "clear")
        append_value = _as_bool(append, "append")
        press_enter_value = _as_bool(press_enter, "press_enter")
        before_url = page.url
        try:
            if append_value or not clear_value:
                locator.focus()
                locator.press("End")
                page.keyboard.insert_text(value)
            else:
                locator.fill(value, timeout=_as_int(timeout_ms, "timeout_ms", minimum=1))
            if press_enter_value:
                locator.press("Enter")
        except Exception as exc:
            raise RuntimeError(f"Type failed for {target}: {exc}") from exc
        actual = self._read_value_sync(locator)
        return f"Typed into {target}; value={_quote(actual)}; " + self._validation_sync(
            page, locator, before_url, target
        )

    def _select_sync(
        self,
        value: Any,
        text: str | None,
        ref: Any,
        selector: str | None,
        timeout_ms: Any,
    ) -> str:
        page = self._select_page_sync(None)
        locator, target = self._resolve_target_sync(page, ref, selector, timeout_ms)
        self._ensure_visible_sync(locator, target, timeout_ms)
        select_value = value
        if isinstance(select_value, str):
            try:
                select_value = json.loads(select_value)
            except json.JSONDecodeError:
                pass
        if isinstance(select_value, list):
            option_kwargs: dict[str, Any] = {"value": select_value}
        elif select_value is not None:
            option_kwargs = {"value": select_value}
        elif text is not None:
            option_kwargs = {"label": text}
        else:
            raise ValueError("Provide value or text for mode='select'.")
        try:
            locator.select_option(
                timeout=_as_int(timeout_ms, "timeout_ms", minimum=1), **option_kwargs
            )
        except Exception as exc:
            raise RuntimeError(f"Select failed for {target}: {exc}") from exc
        selected = locator.evaluate("el => Array.from(el.selectedOptions).map((o) => o.value)")
        return f"Selected {_quote(selected)} in {target}."

    def _scroll_sync(
        self,
        direction: str,
        amount: Any,
        ref: Any,
        selector: str | None,
        scroll_to: str | None,
        timeout_ms: Any,
    ) -> str:
        page = self._select_page_sync(None)
        if ref is not None or selector:
            locator, target = self._resolve_target_sync(page, ref, selector, timeout_ms)
            self._ensure_visible_sync(locator, target, timeout_ms)
            locator.scroll_into_view_if_needed(timeout=_as_int(timeout_ms, "timeout_ms", minimum=1))
            return f"Scrolled {target} into view."
        requested = (scroll_to or direction or "down").strip().casefold()
        amount_value = _as_int(amount, "amount", minimum=0)
        if requested == "top":
            page.evaluate("() => window.scrollTo(0, 0)")
        elif requested == "bottom":
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        else:
            delta = {
                "up": (0, -amount_value),
                "down": (0, amount_value),
                "left": (-amount_value, 0),
                "right": (amount_value, 0),
            }.get(requested)
            if delta is None:
                raise ValueError("direction must be up, down, left, right, top, or bottom")
            page.evaluate("([x, y]) => window.scrollBy(x, y)", list(delta))
        return f"Scrolled page {requested}."

    def _hover_sync(self, ref: Any, selector: str | None, timeout_ms: Any) -> str:
        page = self._select_page_sync(None)
        locator, target = self._resolve_target_sync(page, ref, selector, timeout_ms)
        self._ensure_visible_sync(locator, target, timeout_ms)
        self._ensure_not_obscured_sync(locator, target)
        try:
            locator.hover(timeout=_as_int(timeout_ms, "timeout_ms", minimum=1))
        except Exception as exc:
            raise RuntimeError(f"Hover failed for {target}: {exc}") from exc
        return f"Hovered {target}."

    def _press_sync(self, key: str, ref: Any, selector: str | None, timeout_ms: Any) -> str:
        if not key:
            raise ValueError("key is required for mode='press'.")
        page = self._select_page_sync(None)
        if ref is not None or selector:
            locator, target = self._resolve_target_sync(page, ref, selector, timeout_ms)
            self._ensure_visible_sync(locator, target, timeout_ms)
            locator.press(key, timeout=_as_int(timeout_ms, "timeout_ms", minimum=1))
            return f"Pressed {key!r} on {target}."
        page.keyboard.press(key)
        return f"Pressed {key!r} on the active page."

    def _evaluate_sync(self, script: str) -> str:
        if not script:
            raise ValueError("script is required for mode='eval'.")
        page = self._select_page_sync(None)
        result = page.evaluate(script)
        if result is None:
            return "null"
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)

    def _cookies_sync(self, action: str, payload: Any) -> str:
        context = self._context
        if context is None:
            raise RuntimeError(
                "Browser is not connected. Use mode='connect' or mode='launch' first."
            )
        normalized = (action or "get").strip().casefold()
        if normalized == "get":
            return json.dumps(context.cookies(), ensure_ascii=False, indent=2)
        if normalized == "set":
            cookies = _as_dict_list(payload, "payload")
            context.add_cookies(cookies)
            return f"Set {len(cookies)} cookie(s)."
        if normalized == "clear":
            context.clear_cookies()
            return "Cleared all cookies in the current browser context."
        raise ValueError("cookies action must be get, set, or clear")

    def _detach_network_sync(self) -> None:
        for page, event_name, handler in self._network_handlers:
            try:
                page.remove_listener(event_name, handler)
            except Exception:
                pass
        self._network_handlers.clear()

    def _network_sync(self, action: str, limit: Any) -> str:
        normalized = (action or "get").strip().casefold()
        if normalized == "start":
            page = self._select_page_sync(None)
            self._detach_network_sync()
            self._network_events = []

            def on_request(request: Any) -> None:
                if request.resource_type not in {"xhr", "fetch"}:
                    return
                self._network_events.append(
                    {
                        "kind": "request",
                        "request_id": id(request),
                        "method": request.method,
                        "url": request.url,
                        "resource_type": request.resource_type,
                        "post_data": _truncate(request.post_data, 2000)
                        if request.post_data
                        else None,
                        "timestamp_ms": int(time.time() * 1000),
                    }
                )

            def on_response(response: Any) -> None:
                request = response.request
                if request.resource_type not in {"xhr", "fetch"}:
                    return
                self._network_events.append(
                    {
                        "kind": "response",
                        "request_id": id(request),
                        "status": response.status,
                        "url": response.url,
                        "resource_type": request.resource_type,
                        "headers": {
                            key: _truncate(value, _MAX_ATTR)
                            for key, value in response.headers.items()
                        },
                        "timestamp_ms": int(time.time() * 1000),
                    }
                )

            self._network_handlers = [
                (page, "request", on_request),
                (page, "response", on_response),
            ]
            page.on("request", on_request)
            page.on("response", on_response)
            return "Network capture started for XHR/fetch requests and responses."
        if normalized == "stop":
            count = len(self._network_events)
            self._detach_network_sync()
            return f"Network capture stopped; captured {count} event(s)."
        if normalized == "get":
            maximum = _as_int(limit, "limit", minimum=1)
            return json.dumps(self._network_events[-maximum:], ensure_ascii=False, indent=2)
        raise ValueError("network action must be start, stop, or get")

    def _wait_sync(
        self,
        wait_for: str,
        selector: str | None,
        text: str | None,
        url: str | None,
        timeout_ms: Any,
    ) -> str:
        page = self._select_page_sync(None)
        timeout = _as_int(timeout_ms, "timeout_ms", minimum=1)
        started = time.monotonic()
        normalized = (wait_for or "").strip().casefold()
        if normalized == "selector":
            if not selector:
                raise ValueError("selector is required when wait_for='selector'.")
            page.locator(selector).first.wait_for(state="visible", timeout=timeout)
        elif normalized == "text":
            if not text:
                raise ValueError("text is required when wait_for='text'.")
            page.wait_for_function(
                "expected => (document.body?.innerText || '').includes(expected)",
                arg=text,
                timeout=timeout,
            )
        elif normalized == "url":
            if not url:
                raise ValueError("url is required when wait_for='url'.")
            page.wait_for_url(url, timeout=timeout)
        elif normalized == "networkidle":
            page.wait_for_load_state("networkidle", timeout=timeout)
        elif normalized == "timeout":
            page.wait_for_timeout(timeout)
        else:
            raise ValueError("wait_for must be selector, text, url, networkidle, or timeout")
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return f"Wait condition {normalized!r} satisfied after {elapsed_ms}ms."

    def _screenshot_sync(
        self,
        path: str | None,
        full_page: Any,
        ref: Any,
        selector: str | None,
        timeout_ms: Any,
    ) -> str:
        page = self._select_page_sync(None)
        if path:
            target_path = Path(path).expanduser()
            if not target_path.is_absolute():
                target_path = Path.cwd() / target_path
        else:
            target_path = Path.cwd() / f"web-screenshot-{int(time.time() * 1000)}.png"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if ref is not None or selector:
                locator, target = self._resolve_target_sync(page, ref, selector, timeout_ms)
                self._ensure_visible_sync(locator, target, timeout_ms)
                locator.screenshot(path=str(target_path))
            else:
                page.screenshot(path=str(target_path), full_page=_as_bool(full_page, "full_page"))
        except Exception as exc:
            raise RuntimeError(f"Screenshot failed: {exc}") from exc
        return f"Screenshot saved to {target_path}."

    def _extract_sync(
        self,
        selector: str | None,
        selectors: list[str] | str | None,
        fields: list[str] | str | None,
        attributes: list[str] | str | None,
        max_elements: Any,
    ) -> str:
        page = self._select_page_sync(None)
        css_selectors = _as_string_list(selectors, "selectors")
        if not css_selectors:
            css_selectors = [selector] if selector else ["body"]
        fields_list = _as_string_list(fields, "fields") or ["text"]
        attrs = _as_string_list(attributes, "attributes") or []
        allowed = {"text", "value", "html", "attributes", "links", "table"}
        unknown = [field for field in fields_list if field not in allowed]
        if unknown:
            raise ValueError(
                f"Unsupported extract field(s): {', '.join(unknown)}. "
                f"Use: {', '.join(sorted(allowed))}."
            )
        maximum = _as_int(max_elements, "max_elements", minimum=1)
        result: dict[str, Any] = {}
        for css in css_selectors:
            locator = page.locator(css)
            try:
                count = locator.count()
            except Exception as exc:
                raise ValueError(f"Invalid selector {css!r}: {exc}") from exc
            items: list[dict[str, Any]] = []
            for index in range(min(count, maximum)):
                item = locator.nth(index)
                entry: dict[str, Any] = {}
                for field in fields_list:
                    try:
                        if field == "text":
                            entry["text"] = _truncate(item.inner_text())
                        elif field == "value":
                            entry["value"] = self._read_value_sync(item)
                        elif field == "html":
                            entry["html"] = _truncate(item.inner_html())
                        elif field == "attributes":
                            if attrs:
                                entry["attributes"] = {
                                    name: item.get_attribute(name) for name in attrs
                                }
                            else:
                                entry["attributes"] = item.evaluate(
                                    "el => Object.fromEntries(Array.from(el.attributes)"
                                    ".map((a) => [a.name, a.value]))"
                                )
                        elif field == "links":
                            entry["links"] = item.evaluate(
                                "el => Array.from(el.querySelectorAll('a[href]')).map((a) => ({"
                                "text: (a.innerText || a.textContent || '').trim(), "
                                "href: a.href}))"
                            )
                        elif field == "table":
                            table = item.locator("table").first
                            if table.count() == 0 and item.evaluate("el => el.tagName") != "TABLE":
                                entry["table"] = []
                            else:
                                target = table if table.count() else item
                                entry["table"] = target.evaluate(
                                    "table => Array.from(table.querySelectorAll('tr')).map((row) => "
                                    "Array.from(row.querySelectorAll('th,td')).map((cell) => "
                                    "(cell.innerText || cell.textContent || '').trim()))"
                                )
                    except Exception as exc:
                        entry[field] = f"<extract error: {exc}>"
                items.append(entry)
            result[css] = items
        return json.dumps(
            {"url": page.url, "title": page.title(), "results": result},
            ensure_ascii=False,
            indent=2,
        )


_SERVICE: WebAutomationService | None = None
_SERVICE_LOCK = Lock()


def get_web_service() -> WebAutomationService:
    """Return the process-wide browser automation service singleton."""

    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = WebAutomationService()
    return _SERVICE


__all__ = ["DEFAULT_CDP_ENDPOINT", "WebAutomationService", "get_web_service"]





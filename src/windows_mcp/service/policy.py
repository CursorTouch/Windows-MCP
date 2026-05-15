"""Secure-Desktop consent policy — read from HKLM, written on install/set-policy.

Three policies (per issue #236):

* ``block``            — service exposes the UAC dialog to the agent but
                         REFUSES to auto-click Yes/No.  Default.
* ``allow_with_match`` — auto-click only if the requesting binary's publisher
                         (as it appears in the UAC dialog) substring-matches
                         one of ``publishers_allowlist``.
* ``allow_all``        — auto-click any UAC prompt.  Only for sandboxed VMs.

The policy is persisted in the registry so that the LocalSystem service can
read it without any inheritance from the broker's user environment.  The
broker also reads it to pre-filter requests before they ever leave the user
session (defense in depth — even a tampered broker cannot bypass the SYSTEM
service's check).

Registry layout::

    HKLM\\SOFTWARE\\Windows-MCP\\SecureDesktop
        Policy               REG_SZ        "block" | "allow_with_match" | "allow_all"
        PublishersAllowlist  REG_MULTI_SZ  ["Microsoft Corporation", ...]
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

POLICIES = ("block", "allow_with_match", "allow_all")
DEFAULT_POLICY = "block"

ENV_POLICY = "WINDOWS_MCP_SECURE_DESKTOP_POLICY"
ENV_ALLOWLIST = "WINDOWS_MCP_SECURE_DESKTOP_ALLOWLIST"

_REG_PATH = r"SOFTWARE\Windows-MCP\SecureDesktop"
_REG_POLICY = "Policy"
_REG_ALLOWLIST = "PublishersAllowlist"
_REG_UIA_WORKER = "UiaWorkerPath"


@dataclass
class SecureDesktopPolicy:
    policy: str = DEFAULT_POLICY
    publishers_allowlist: list[str] = field(default_factory=list)

    def allows_auto_click(self, publisher: str | None) -> tuple[bool, str]:
        """Return ``(allowed, reason)`` for an auto-click attempt on the Secure Desktop.

        ``publisher`` is the "Verified publisher" string from the UAC dialog
        (or ``None`` if it could not be determined).
        """
        if self.policy == "allow_all":
            return True, "policy=allow_all"
        if self.policy == "block":
            return False, "policy=block"
        # allow_with_match
        if publisher is None:
            return False, "publisher unknown; allow_with_match requires a match"
        for needle in self.publishers_allowlist:
            if needle and needle.lower() in publisher.lower():
                return True, f"publisher {publisher!r} matched allowlist entry {needle!r}"
        return False, f"publisher {publisher!r} not in allowlist"


def _validate_policy(value: str) -> str:
    v = value.strip().lower()
    if v not in POLICIES:
        raise ValueError(
            f"Invalid policy {value!r}; must be one of {POLICIES}"
        )
    return v


def from_env() -> SecureDesktopPolicy | None:
    """Build a policy from environment variables, or return None if unset."""
    raw = os.environ.get(ENV_POLICY)
    if not raw:
        return None
    policy = _validate_policy(raw)
    raw_list = os.environ.get(ENV_ALLOWLIST, "")
    allowlist = [s.strip() for s in raw_list.split(",") if s.strip()]
    return SecureDesktopPolicy(policy=policy, publishers_allowlist=allowlist)


def read_from_registry() -> SecureDesktopPolicy:
    """Read the persisted policy.  Returns the default policy on any failure."""
    try:
        import winreg
    except ImportError:
        return SecureDesktopPolicy()
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, _REG_PATH, access=winreg.KEY_READ
        ) as key:
            try:
                policy_raw, _ = winreg.QueryValueEx(key, _REG_POLICY)
                policy = _validate_policy(str(policy_raw))
            except FileNotFoundError:
                policy = DEFAULT_POLICY
            try:
                allowlist_raw, _ = winreg.QueryValueEx(key, _REG_ALLOWLIST)
                allowlist = [s for s in (allowlist_raw or []) if s]
            except FileNotFoundError:
                allowlist = []
    except FileNotFoundError:
        return SecureDesktopPolicy()
    except OSError as exc:
        logger.warning("Reading policy from registry failed: %s", exc)
        return SecureDesktopPolicy()
    return SecureDesktopPolicy(policy=policy, publishers_allowlist=allowlist)


def write_to_registry(policy: SecureDesktopPolicy) -> None:
    """Persist *policy* to HKLM.  Requires elevation."""
    import winreg
    _validate_policy(policy.policy)
    with winreg.CreateKeyEx(
        winreg.HKEY_LOCAL_MACHINE, _REG_PATH, access=winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, _REG_POLICY, 0, winreg.REG_SZ, policy.policy)
        winreg.SetValueEx(
            key, _REG_ALLOWLIST, 0, winreg.REG_MULTI_SZ, list(policy.publishers_allowlist)
        )
    logger.info(
        "Wrote secure-desktop policy=%s allowlist=%s", policy.policy, policy.publishers_allowlist
    )


def delete_from_registry() -> None:
    """Remove the persisted policy.  Used on service uninstall."""
    try:
        import winreg
    except ImportError:
        return
    try:
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, _REG_PATH)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not delete policy registry key: %s", exc)


def read_uia_worker_path() -> str | None:
    """Read the path of the installed UIAccess-signed worker, if any.

    Returns ``None`` when no signed worker is registered. The service falls
    back to spawning ``python -m windows_mcp.service.user_session_worker``
    directly in that case — which works for everything *except* walking the
    Winlogon UIA tree across the integrity-level boundary (consent.exe runs
    at System integrity, and only a manifested + signed binary in a
    trusted path is granted UIAccess). See docs/secure-desktop.md.
    """
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, _REG_PATH, access=winreg.KEY_READ
        ) as key:
            value, _ = winreg.QueryValueEx(key, _REG_UIA_WORKER)
            value = str(value).strip()
            return value or None
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Reading UIA worker path from registry failed: %s", exc)
        return None


def write_uia_worker_path(path: str) -> None:
    """Persist the installed UIAccess worker's path to HKLM. Requires elevation."""
    import winreg
    with winreg.CreateKeyEx(
        winreg.HKEY_LOCAL_MACHINE, _REG_PATH, access=winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, _REG_UIA_WORKER, 0, winreg.REG_SZ, path)
    logger.info("Recorded UIA worker path: %s", path)


def resolve_install_time_policy(
    cli_policy: str | None,
    cli_allowlist: list[str] | None,
    config_policy: str | None,
    config_allowlist: list[str] | None,
) -> SecureDesktopPolicy:
    """Merge CLI flag, env var, TOML config, and default — in that precedence order."""
    env = from_env()

    if cli_policy is not None:
        policy = _validate_policy(cli_policy)
    elif env is not None:
        policy = env.policy
    elif config_policy is not None:
        policy = _validate_policy(config_policy)
    else:
        policy = DEFAULT_POLICY

    if cli_allowlist:
        allowlist = list(cli_allowlist)
    elif env is not None and env.publishers_allowlist:
        allowlist = env.publishers_allowlist
    elif config_allowlist:
        allowlist = list(config_allowlist)
    else:
        allowlist = []

    return SecureDesktopPolicy(policy=policy, publishers_allowlist=allowlist)

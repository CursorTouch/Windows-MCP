"""Serialized, self-healing access to the comtypes generated-module cache.

comtypes generates Python wrappers for COM type libraries on first use and
stores them in ``comtypes/gen``. When two processes generate that cache
concurrently (e.g. an MCP host spawning multiple server instances, see
issue #357), or generation is interrupted, the cache is left corrupted and
every subsequent startup fails with ImportError/AttributeError.

``safe_get_module`` wraps ``comtypes.client.GetModule`` with

1. a cross-process file lock so only one process generates at a time, and
2. automatic cache clearing + regeneration when corruption is detected.
"""

import errno
import hashlib
import msvcrt
import os
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from types import ModuleType
from typing import Iterator, Optional

import comtypes.client

_LOCK_PATH: str = os.path.join(
    tempfile.gettempdir(),
    "comtypes-gen-%s.lock" % hashlib.md5(sys.prefix.encode("utf-8")).hexdigest(),
)

# msvcrt.locking(LK_LOCK) retries for ~10s and then raises OSError with one of
# these errno values while the lock is still held by another process.
_CONTENTION_ERRNOS: tuple[int, ...] = (errno.EACCES, errno.EDEADLK)

_LOCK_TIMEOUT_SECONDS: float = 120.0


@contextmanager
def comtypes_cache_lock(timeout: float = _LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Cross-process lock serializing comtypes.gen cache generation.

    Args:
        timeout: Maximum number of seconds to wait for the lock before
            giving up.

    Yields:
        None. The lock is held for the duration of the ``with`` block.

    Raises:
        TimeoutError: If the lock could not be acquired within ``timeout``
            seconds.
        OSError: For locking failures that are not lock contention (e.g. an
            invalid handle); re-raised immediately.
    """
    fd = os.open(_LOCK_PATH, os.O_CREAT | os.O_RDWR)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                # Blocks and retries for ~10s per call, then raises OSError.
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                break
            except OSError as ex:
                if ex.errno not in _CONTENTION_ERRNOS:
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "timed out after %.0fs waiting for comtypes cache "
                        "lock %r" % (timeout, _LOCK_PATH)
                    ) from ex
        yield
    finally:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        os.close(fd)


def _clear_gen_cache() -> None:
    """Best-effort removal of generated modules so comtypes can regenerate.

    Never raises: this runs on the corruption-recovery path, where a failure
    to clear should fall through to the retry rather than mask the original
    error with a new one.
    """
    try:
        import comtypes.gen as gen

        gen_dir = os.path.dirname(gen.__file__ or "")
        entries = os.listdir(gen_dir)
    except Exception:
        return
    for name in entries:
        if name == "__init__.py":
            continue
        path = os.path.join(gen_dir, name)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except OSError:
            pass
    for mod_name in list(sys.modules):
        if mod_name.startswith("comtypes.gen."):
            del sys.modules[mod_name]


def safe_get_module(tlib: str, required_attr: Optional[str] = None) -> ModuleType:
    """``comtypes.client.GetModule`` with locking and corrupt-cache recovery.

    Args:
        tlib: Type library to generate a wrapper for, e.g. a DLL name such
            as ``"UIAutomationCore.dll"``.
        required_attr: Optional name of an attribute the generated module
            must expose. A partially generated cache that imports but lacks
            the attribute is treated as corrupted and regenerated as well.

    Returns:
        The generated ``comtypes.gen`` friendly module for ``tlib``.

    Raises:
        ImportError: If the cache is corrupted and regeneration failed.
        AttributeError: If ``required_attr`` is still missing after
            regeneration.
        TimeoutError: If the cross-process cache lock could not be acquired.
    """
    with comtypes_cache_lock():
        for attempt in (0, 1):
            try:
                module = comtypes.client.GetModule(tlib)
                if required_attr is not None and not hasattr(module, required_attr):
                    raise AttributeError(
                        "generated module %r lacks %r (corrupted cache)"
                        % (getattr(module, "__name__", tlib), required_attr)
                    )
                return module
            except (ImportError, AttributeError):
                if attempt:
                    raise
                _clear_gen_cache()
    raise AssertionError("unreachable")  # pragma: no cover

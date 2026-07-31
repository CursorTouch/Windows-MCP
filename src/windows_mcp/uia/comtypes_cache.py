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

import hashlib
import msvcrt
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager

import comtypes.client

_LOCK_PATH = os.path.join(
    tempfile.gettempdir(),
    "comtypes-gen-%s.lock" % hashlib.md5(sys.prefix.encode("utf-8")).hexdigest(),
)


@contextmanager
def comtypes_cache_lock():
    """Cross-process lock serializing comtypes.gen cache generation."""
    fd = os.open(_LOCK_PATH, os.O_CREAT | os.O_RDWR)
    try:
        while True:
            try:
                # Blocks and retries for ~10s, then raises OSError; loop
                # until the other process releases the lock.
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                break
            except OSError:
                continue
        yield
    finally:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        os.close(fd)


def _clear_gen_cache() -> None:
    """Delete generated modules so comtypes can regenerate them cleanly."""
    try:
        import comtypes.gen as gen

        gen_dir = os.path.dirname(gen.__file__)
    except Exception:
        return
    for name in os.listdir(gen_dir):
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


def safe_get_module(tlib, required_attr=None):
    """``comtypes.client.GetModule`` with locking and corrupt-cache recovery.

    ``required_attr`` optionally names an attribute the generated module must
    expose; a partially generated cache that imports but lacks it is treated
    as corrupted and regenerated as well.
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

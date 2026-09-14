"""In-process guards for a T1 replay worker.

These are Python-level tripwires, not an operating-system sandbox. They make an
accidental network call, process launch, native desktop/GPU access, write outside
the owned root, read of live Tracker data or import of a server-side module fail
loudly inside the worker, and the runner records proof that they were active.
"""
from __future__ import annotations

import builtins
import ctypes
import importlib.abc
import io
import multiprocessing.process
import os
import socket
import subprocess
import sys

from .paths import is_within

# Server-side and native modules a replay worker has no reason to import. The
# data-root side effects of diagnostics/app_paths on import are why this matters.
DENIED_IMPORTS = frozenset((
    "server", "diagnostics", "app", "app_paths", "launcher", "dashboard", "control",
    "effect_screenshot", "game_overlay", "settings_window", "history_store", "history_analytics",
    "stats_manager", "config_utils", "pending_history", "event_bus",
    "windows_capture", "cv2",
))
DENIED_DLLS = frozenset(("user32", "gdi32", "dwmapi", "d3d11", "dxgi", "shcore", "winmm",
                         "ws2_32", "wininet", "winhttp", "dnsapi", "iphlpapi", "mswsock"))


class GuardViolation(RuntimeError):
    """A T1 worker attempted something replay must never do."""


def _deny(what):
    def denied(*args, **kwargs):
        raise GuardViolation(f"T1 replay does not allow {what}")
    return denied


class _DeniedImports(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".", 1)[0] in DENIED_IMPORTS:
            raise GuardViolation(f"T1 replay does not allow importing {name}")
        return None


def _dll_name(name):
    base = os.path.basename(os.fspath(name)) if name is not None else ""
    base = base.lower()
    return base[:-4] if base.endswith(".dll") else base


def _writes(mode):
    return any(flag in str(mode) for flag in ("w", "a", "x", "+"))


def install(owned_root, forbidden_roots=()):
    """Install every guard; return a callable that restores the originals."""
    owned_root = os.fspath(owned_root)
    forbidden_roots = tuple(os.fspath(root) for root in forbidden_roots)
    saved = []

    def replace(holder, name, value):
        saved.append((holder, name, getattr(holder, name)))
        setattr(holder, name, value)

    def check_path(path, writing):
        if isinstance(path, int):
            return
        path = os.fsdecode(os.fspath(path))
        for root in forbidden_roots:
            if is_within(root, path):
                raise GuardViolation("T1 replay does not allow touching live Tracker data")
        if writing and not is_within(owned_root, path):
            raise GuardViolation("T1 replay does not allow writing outside its owned root")

    original_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):
        check_path(file, _writes(mode))
        return original_open(file, mode, *args, **kwargs)

    original_os_open = os.open
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

    def guarded_os_open(path, flags, *args, **kwargs):
        check_path(path, bool(flags & write_flags))
        return original_os_open(path, flags, *args, **kwargs)

    def guarded_mutation(original):
        def mutation(*paths, **kwargs):
            for path in paths[:2]:
                check_path(path, True)
            return original(*paths, **kwargs)
        return mutation

    replace(builtins, "open", guarded_open)
    replace(io, "open", guarded_open)
    replace(os, "open", guarded_os_open)
    for name in ("remove", "unlink", "rmdir", "rename", "replace", "mkdir", "makedirs"):
        replace(os, name, guarded_mutation(getattr(os, name)))

    # Network: refuse to create sockets or resolve names at all.
    class DeniedSocket:
        def __init__(self, *args, **kwargs):
            raise GuardViolation("T1 replay does not allow sockets")

    replace(socket, "socket", DeniedSocket)
    for name in ("create_connection", "create_server", "getaddrinfo", "gethostbyname", "gethostbyname_ex",
                 "gethostbyaddr", "socketpair", "fromfd"):
        if hasattr(socket, name):
            replace(socket, name, _deny(f"socket.{name}"))

    # Processes: the documented entry points and the Windows primitive beneath them.
    class DeniedPopen:
        def __init__(self, *args, **kwargs):
            raise GuardViolation("T1 replay does not allow launching processes")

    replace(subprocess, "Popen", DeniedPopen)
    for name in ("system", "popen", "startfile", "spawnl", "spawnle", "spawnv", "spawnve",
                 "execv", "execve", "execl", "execle", "execlp", "execvp", "fork"):
        if hasattr(os, name):
            replace(os, name, _deny(f"os.{name}"))
    replace(multiprocessing.process.BaseProcess, "start", _deny("multiprocessing.Process.start"))
    if os.name == "nt":
        import _winapi
        replace(_winapi, "CreateProcess", _deny("_winapi.CreateProcess"))

    # Native desktop, window and GPU libraries.
    def guarded_loader(base):
        class Guarded(base):
            def __init__(self, name, *args, **kwargs):
                if _dll_name(name) in DENIED_DLLS:
                    raise GuardViolation(f"T1 replay does not allow loading {_dll_name(name)}")
                super().__init__(name, *args, **kwargs)
        return Guarded

    replace(ctypes, "CDLL", guarded_loader(ctypes.CDLL))
    if os.name == "nt":
        guarded_windll = guarded_loader(ctypes.WinDLL)
        replace(ctypes, "WinDLL", guarded_windll)
        replace(ctypes, "windll", ctypes.LibraryLoader(guarded_windll))
        replace(ctypes, "oledll", ctypes.LibraryLoader(guarded_loader(ctypes.OleDLL)))
    replace(ctypes, "cdll", ctypes.LibraryLoader(ctypes.CDLL))

    finder = _DeniedImports()
    sys.meta_path.insert(0, finder)

    def restore():
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)
        for holder, name, value in reversed(saved):
            setattr(holder, name, value)

    return restore


def probe(owned_root, outside_path):
    """Exercise each guard without doing the guarded thing; return what held."""
    results = {}

    def expect_violation(name, action):
        try:
            action()
        except GuardViolation:
            results[name] = True
        except Exception as error:  # a different failure is not proof the guard held
            results[name] = f"unexpected {type(error).__name__}"
        else:
            results[name] = False

    expect_violation("socket", lambda: socket.socket())
    expect_violation("dns", lambda: socket.getaddrinfo("example.invalid", 80))
    expect_violation("process", lambda: subprocess.Popen([sys.executable, "-c", "pass"]))
    expect_violation("write_outside_root", lambda: open(outside_path, "w"))
    expect_violation("denied_import", lambda: __import__("server"))
    if os.name == "nt":
        expect_violation("native_window_dll", lambda: ctypes.WinDLL("user32"))
    return results

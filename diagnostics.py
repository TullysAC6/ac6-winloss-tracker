from __future__ import annotations

import json
import importlib.metadata
import importlib.util
import os
import platform
import sqlite3
import struct
import sys
import threading
import time
import zipfile
from collections import deque
from pathlib import Path

from app_paths import VERSION, data_dir, diagnostics_dir
from history_store import read_history_schema_version

try:
    from mss.tools import to_png
except ImportError:
    to_png = None


class DiagnosticRecorder:
    """Privacy-minimized local diagnostics for detector troubleshooting.

    Stores compact detector telemetry and only the small result-detection ROI,
    never the full screen. Files stay local until the user explicitly exports.
    """

    MAX_LOG_BYTES = 5 * 1024 * 1024
    MAX_IMAGES = 40
    FRAME_BUFFER_SIZE = 240

    def __init__(self):
        self.root = diagnostics_dir()
        self.log_path = self.root / "detector.jsonl"
        self.old_log_path = self.root / "detector.previous.jsonl"
        self.image_dir = self.root / "roi"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.frame_buffer = deque(maxlen=self.FRAME_BUFFER_SIZE)

    def _rotate(self):
        try:
            if self.log_path.exists() and self.log_path.stat().st_size >= self.MAX_LOG_BYTES:
                try:
                    self.old_log_path.unlink()
                except FileNotFoundError:
                    pass
                os.replace(self.log_path, self.old_log_path)
        except OSError:
            pass

    def _row(self, kind: str, **payload):
        return {
            "ts": time.time(),
            "kind": kind,
            **payload,
        }

    def _append_rows(self, rows):
        if not rows:
            return
        self._rotate()
        with self.log_path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def record(self, kind: str, **payload):
        row = self._row(kind, **payload)
        with self.lock:
            try:
                self._append_rows([row])
            except OSError:
                pass

    def buffer_frame(self, **payload):
        """Keep routine frame telemetry in memory until context is useful."""
        with self.lock:
            self.frame_buffer.append(self._row("frame", **payload))

    def flush_frame_context(self, reason: str):
        """Persist and clear the recent frame window around an important event."""
        with self.lock:
            if not self.frame_buffer:
                return 0
            rows = [
                {**row, "kind": "frame_context", "context_reason": str(reason)}
                for row in self.frame_buffer
            ]
            try:
                self._append_rows(rows)
            except OSError:
                return 0
            self.frame_buffer.clear()
            return len(rows)

    def buffered_frames(self):
        with self.lock:
            return [dict(row) for row in self.frame_buffer]

    def _write_buffer_export(self):
        path = self.root / "frame-buffer.jsonl"
        try:
            with path.open("w", encoding="utf-8") as f:
                for row in self.buffered_frames():
                    f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            return path
        except OSError:
            return None

    def capture_roi(self, shot, label: str) -> str | None:
        if to_png is None:
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        millis = int((time.time() % 1) * 1000)
        safe = "".join(c for c in label.lower() if c.isalnum() or c in "_-")[:32] or "roi"
        path = self.image_dir / f"{stamp}-{millis:03d}-{safe}.png"
        try:
            to_png(shot.rgb, shot.size, output=str(path))
            self._prune_images()
            return path.name
        except Exception:
            return None

    def _prune_images(self):
        try:
            files = sorted(self.image_dir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in files[self.MAX_IMAGES:]:
                try:
                    path.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    def export(self) -> Path:
        root = data_dir()
        export_dir = Path.home() / "Desktop"
        if not export_dir.exists():
            export_dir = root
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        out = export_dir / f"AC6-Tracker-Diagnostics-{stamp}.zip"

        dependencies = {}
        for dependency in ("mss", "ttkbootstrap"):
            try:
                dependencies[dependency] = {
                    "available": importlib.util.find_spec(dependency) is not None,
                    "version": importlib.metadata.version(dependency),
                }
            except Exception as error:
                dependencies[dependency] = {
                    "available": False,
                    "version": None,
                    "error_type": type(error).__name__,
                }

        display = {"available": False}
        if os.name == "nt":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                display = {
                    "available": True,
                    "primary_width": int(user32.GetSystemMetrics(0)),
                    "primary_height": int(user32.GetSystemMetrics(1)),
                    "monitor_count": int(user32.GetSystemMetrics(80)),
                    "dpi": int(user32.GetDpiForSystem()) if hasattr(user32, "GetDpiForSystem") else None,
                    "hdr": "not queried",
                }
            except Exception as error:
                display = {"available": False, "error_type": type(error).__name__}

        try:
            history_schema = read_history_schema_version(root)
        except Exception as error:
            history_schema = f"unreadable:{type(error).__name__}"

        manifest = {
            "app_version": VERSION,
            "created_at": time.time(),
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "python_architecture": f"{struct.calcsize('P') * 8}-bit",
            "python_source": "source-distribution" if not getattr(sys, "frozen", False) else "frozen",
            "dependency_status": dependencies,
            "sqlite_runtime_version": sqlite3.sqlite_version,
            "history_schema_version": history_schema,
            "python": {
                "version": platform.python_version(),
                "implementation": platform.python_implementation(),
                "architecture_bits": struct.calcsize("P") * 8,
                "source": "python-source-distribution" if not getattr(sys, "frozen", False) else "frozen",
                "dependencies": dependencies,
            },
            "sqlite": {
                "runtime_version": sqlite3.sqlite_version,
                "history_schema": history_schema,
            },
            "display": display,
            "privacy": "Contains detector telemetry and result-detection ROI images only; no full-screen capture.",
        }
        tmp_manifest = self.root / "manifest.json"
        tmp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        buffer_export = self._write_buffer_export()
        try:
            with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for path in (
                    self.log_path, self.old_log_path, tmp_manifest, buffer_export,
                    root / "config.json", root / "stats.json",
                ):
                    if path is not None and path.exists():
                        zf.write(path, arcname=path.name)
                if self.image_dir.exists():
                    for path in sorted(self.image_dir.glob("*.png")):
                        zf.write(path, arcname=f"roi/{path.name}")
        finally:
            if buffer_export is not None:
                try:
                    buffer_export.unlink()
                except OSError:
                    pass
        return out


RECORDER = DiagnosticRecorder()

from __future__ import annotations

import json
import os
import platform
import shutil
import threading
import time
import zipfile
from collections import deque
from pathlib import Path

from app_paths import VERSION, data_dir, diagnostics_dir

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

        manifest = {
            "app_version": VERSION,
            "created_at": time.time(),
            "platform": platform.platform(),
            "python": platform.python_version(),
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

"""Real Tk/process lifecycle, with an isolated HTTP Tracker stand-in."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def ui(mode):
    import tkinter as tk
    loader = importlib.machinery.SourceFileLoader("gui_launcher", str(ROOT / "launcher.pyw"))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    root = tk.Tk()
    tk.Tk = lambda: root
    errors = []
    original_after = root.after
    main_thread = threading.get_ident()

    def checked_after(*args, **kwargs):
        if threading.get_ident() != main_thread:
            errors.append("worker touched Tk")
        return original_after(*args, **kwargs)

    root.after = checked_after
    module.launch_once = lambda: "started" if mode == "started" else "already_running"
    if mode == "startup_race":
        def finish_after_shutdown():
            module.shutdown_tracker()
            return "already_running"
        module.launch_once = finish_after_shutdown
    if mode == "failure":
        module.shutdown_tracker = lambda: False
    elif mode == "partial_failure":
        shutdown = module.shutdown_tracker
        def partial_failure():
            shutdown()
            return False
        module.shutdown_tracker = partial_failure
    elif mode == "exception":
        def fail():
            raise RuntimeError("test worker failure")
        module.shutdown_tracker = fail

    def widgets(parent):
        for child in parent.winfo_children():
            yield child
            yield from widgets(child)

    def click():
        buttons = [w for w in widgets(root) if isinstance(w, tk.Button)
                   and w.cget("text") == "Trackerを終了"]
        if not buttons or not buttons[0].winfo_ismapped():
            original_after(50, click)
            return
        Path(os.environ["LOCALAPPDATA"], mode + ".ready").touch()
        if mode not in ("peer", "startup_race"):
            buttons[0].invoke()

    def check_failure():
        text = " ".join(w.cget("text") for w in widgets(root) if isinstance(w, tk.Label))
        if "完全に終了できませんでした" not in text:
            errors.append("failure result was not displayed")
        root.destroy()

    original_after(150, click)
    if mode in ("failure", "exception", "partial_failure"):
        original_after(2500, check_failure)
    module.main()
    if errors:
        raise AssertionError(errors)


SERVER = '''
import json, os, threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
path = Path(os.environ["LOCALAPPDATA"]) / "AC6WinLossTracker" / ".runtime.json"
path.parent.mkdir(parents=True, exist_ok=True)
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok": true}')
    def do_POST(self):
        assert self.headers.get("X-Control-Token") == "fixture"
        self.send_response(200); self.end_headers()
        threading.Thread(target=server.shutdown, daemon=True).start()
server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
path.write_text(json.dumps(dict(pid=os.getpid(), port=server.server_port, token="fixture")))
server.serve_forever()
server.server_close()
path.unlink(missing_ok=True)
'''


@unittest.skipUnless(os.name == "nt", "native Windows Tk lifecycle")
class LauncherGuiLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ac6-launcher-gui-")
        self.env = dict(os.environ, LOCALAPPDATA=self.tmp.name, PYTHONUTF8="1")
        self.children = []
        self.logs = []
        self.server = self.start(["-c", SERVER])
        self.wait_for(Path(self.tmp.name, "AC6WinLossTracker", ".runtime.json"))

    def start(self, args, executable=None):
        log = tempfile.TemporaryFile()
        self.logs.append(log)
        child = subprocess.Popen([executable or sys.executable, *args], env=self.env, stdout=log, stderr=log)
        self.children.append(child)
        return child

    def wait_for(self, path):
        deadline = time.monotonic() + 8
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertTrue(path.exists(), str(path))

    def start_ui(self, mode):
        return self.start([str(Path(__file__).resolve()), "--ui", mode],
                          str(Path(sys.executable).with_name("pythonw.exe")))

    def exited(self, child):
        self.assertEqual(child.wait(timeout=8), 0)

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        for log in self.logs:
            log.seek(0)
            output = log.read().decode("utf-8", "replace")
            if output:
                print(output)
            log.close()
        self.tmp.cleanup()

    def test_shutdown_exits_clicked_launcher(self):
        for cycle in range(3):
            with self.subTest(cycle=cycle):
                if cycle:
                    self.server = self.start(["-c", SERVER])
                    self.wait_for(Path(self.tmp.name, "AC6WinLossTracker", ".runtime.json"))
                self.exited(self.start_ui("started"))
                self.exited(self.start_ui("shutdown"))
                self.exited(self.server)

    def test_shutdown_also_closes_other_status_launcher(self):
        peer = self.start_ui("peer")
        self.wait_for(Path(self.tmp.name, "peer.ready"))
        self.exited(self.start_ui("shutdown"))
        self.exited(self.server)
        self.exited(peer)

    def test_abnormal_tracker_exit_closes_status_launcher(self):
        peer = self.start_ui("peer")
        self.wait_for(Path(self.tmp.name, "peer.ready"))
        self.server.kill()
        self.server.wait(timeout=5)
        self.exited(peer)

    def test_failure_remains_visible_and_can_close(self):
        self.exited(self.start_ui("failure"))
        self.assertIsNone(self.server.poll())

    def test_worker_exception_displays_failure(self):
        self.exited(self.start_ui("exception"))
        self.assertIsNone(self.server.poll())

    def test_partial_failure_is_not_hidden_by_process_watcher(self):
        self.exited(self.start_ui("partial_failure"))
        self.exited(self.server)

    def test_shutdown_between_readiness_and_ui_result(self):
        self.exited(self.start_ui("startup_race"))
        self.exited(self.server)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--ui":
        ui(sys.argv[2])
    else:
        unittest.main()

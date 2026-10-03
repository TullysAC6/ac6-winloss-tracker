"""T2: authenticated isolated server and actual contained short-lived worker lifecycle."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from python_spawn import spawn_python
from owned_worker import KillOnCloseJob, stop_process
from lobby_capture import acquire, ProcessAdapter, process_birth
from metadata_runtime import MetadataRuntime
from t1.process import WorkerJob
from test_rollback_previous_version import extract_previous

# Structural input only; these bytes are NOT recognition fixture truth.
TARGET = {"hwnd": 10, "pid": 20, "birth": 30,
          "client": {"left": 0, "top": 0, "width": 1920, "height": 1080},
          "bounds": [0,0,1920,1080]}

PHASE = r'''
import builtins, json, os, sqlite3, sys, threading
from pathlib import Path
from unittest.mock import patch
source, root = Path(sys.argv[1]), Path(sys.argv[2])
assert sys.stdin.readline().strip() == "GO"
sys.path.insert(0, str(source))
sys.path.insert(0, str(source / "tests"))
import config_utils
config_utils.CONFIG_PATH.write_text(json.dumps(dict(config_utils.DEFAULT_CONFIG,
    port=int(sys.argv[3]), result_detector_enabled=False)), encoding="utf-8")
import server
ready, failures = threading.Event(), []
def run():
    try:
        server.main(on_ready=ready.set)
    except BaseException as error:
        failures.append(str(error)); ready.set()
thread = threading.Thread(target=run, daemon=True)
# OFF startup must not even import the optional modules.
def guarded(original):
    def check(path, *args, **kwargs):
        assert "enrichment.db" not in str(path), "OFF touched sidecar"
        return original(path, *args, **kwargs)
    return check
with patch.object(sqlite3, "connect", guarded(sqlite3.connect)), \
     patch.object(Path, "stat", guarded(Path.stat)):
    thread.start()
    assert ready.wait(20) and not failures, failures
assert "metadata_runtime" not in sys.modules
assert "optional_enrichment" not in sys.modules
assert "lobby_capture" not in sys.modules
print("READY " + json.dumps({"pid":os.getpid(),"token":server.CONTROL_TOKEN}), flush=True)
# Parent drives the authenticated real HTTP handler. Fake capture is explicitly
# injected only after opt-in; production routes never accept these dependencies.
from metadata_runtime import MetadataRuntime, VERSION
from enrichment_test_clock import fixture_clock
clock = [100.0]
target = {"hwnd":10,"pid":20,"birth":30,"client":{"left":0,"top":0,"width":1920,"height":1080},
          "bounds":[0,0,1920,1080]}
def worker(t,c):
    return {"status":"recognized","match_type":"ranked","match_format":"single",
            "version":VERSION,"source":"direct_header","captured_at":clock[0],"cleaned":True}
def finish():
    task = server.metadata_runtime.task
    if task: task.join(5); assert not task.is_alive()
with fixture_clock():
    for line in sys.stdin:
        message = json.loads(line)
        cmd = message["cmd"]
        if cmd == "install":
            server.metadata_runtime = MetadataRuntime(server.DATA_ROOT,
                target=lambda: target, worker=worker, clock=lambda: clock[0])
        elif cmd == "native_install":
            from lobby_capture import acquire
            script = message["script"]
            timing = {"clock": lambda: clock[0]} if message.get("fake_clock") else {}
            server.metadata_runtime = MetadataRuntime(server.DATA_ROOT, target=lambda: target,
                worker=lambda t,c: acquire(t,c,script=script), **timing)
        elif cmd == "finish": finish()
        elif cmd == "result":
            server.result_gate.clear_for_manual_correction()
            assert server.record_result(message["result"], "auto")
        elif cmd == "draw": server.metadata_notify("result", result="draw")
        elif cmd == "gap": server.metadata_notify("capture_gap")
        elif cmd == "expire": clock[0] += message["seconds"]
        elif cmd == "sidecar_fault":
            def broken(root): raise OSError("injected optional fault")
            server.metadata_runtime.service_factory = broken
        elif cmd == "locked":
            with server.metadata_runtime.lock:
                server.result_gate.clear_for_manual_correction()
                assert server.record_result("win", "auto")
        elif cmd == "rows":
            c = sqlite3.connect(server.DATA_ROOT / "history.db")
            message["rows"] = c.execute("SELECT event_id,result,created_at FROM matches ORDER BY id").fetchall(); c.close()
        elif cmd == "diagnostic_composition":
            import zipfile
            import result_detector as rd
            from diagnostics import DiagnosticRecorder
            from composition_helpers import detector_sequence
            recorder = DiagnosticRecorder()
            results, states, order = detector_sequence(recorder, server.metadata_notify,
                [rd.CLEAR]*3 + [rd.FINAL_WIN, rd.CLEAR])
            assert results == [] and order == []
            writer = recorder._candidate_writer
            assert writer is not None and writer.close(timeout=2)
            assert not writer.thread.is_alive()
            bundles = list(writer.root.glob("*.zip"))
            assert len(bundles) == 1 and not list(writer.root.glob("*.partial"))
            with zipfile.ZipFile(bundles[0]) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            message["frames"] = [f["context"]["frame_state"] for f in manifest["frames"]]
            message["writer_dead"] = not writer.thread.is_alive()
        elif cmd == "stop":
            server.server_stop = True
            break
        print("ACK " + json.dumps(message), flush=True)
thread.join(8)
assert not thread.is_alive(), "server did not exit"
assert not server.RUNTIME_PATH.exists()
print("DONE", flush=True)
'''

FAKE_WORKER = r'''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, SOURCE)
from lobby_worker import Parent, Ready
from owned_worker import owned_entry
owner = Parent(int(sys.argv[1]), int(sys.argv[2]))
ready = Ready()
print(json.dumps({"pid":os.getpid(),"launch_nonce":os.environ.get("AC6_LAUNCH_NONCE", "")}), flush=True)
def work():
    assert "windows_capture" not in sys.modules and "server" not in sys.modules
    MODE
    print(json.dumps({"status":"recognized","match_type":"ranked","match_format":"single",
        "version":"rank-single-header.v1","source":"direct_header","captured_at":time.monotonic()}), flush=True)
try: owned_entry(ready, work, (), owner=owner)
finally: owner.close()
'''


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ac6-metadata-t2-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        data = self.root / "AC6WinLossTracker"
        data.mkdir()
        with socket.socket() as free:
            free.bind(("127.0.0.1",0)); self.port = free.getsockname()[1]
        env = dict(os.environ, LOCALAPPDATA=str(self.root))
        self.p = spawn_python(["-u","-c",PHASE,str(ROOT),str(self.root),str(self.port)], environment=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        self.job = WorkerJob(); self.job.assign(self.p.pid)
        self.addCleanup(self.cleanup)
        self.p.stdin.write("GO\n"); self.p.stdin.flush()
        line = self.read("READY ")
        self.token = json.loads(line.removeprefix("READY "))["token"]

    def read(self, prefix):
        # Stdout drained by one bounded reader; lifecycle timeout, not feature TTL.
        box = []
        def run():
            while line := self.p.stdout.readline():
                # Startup's existing stdout banner may precede our marker on
                # the same line. This is framing, not a timing/readiness retry.
                if prefix in line:
                    box.append(line[line.index(prefix):].strip()); return
                self.output.append(line if '"token"' not in line else "[redacted handshake]\n")
        if not hasattr(self,"output"): self.output=[]
        t=threading.Thread(target=run, daemon=True); t.start(); t.join(25)
        self.assertFalse(t.is_alive(), "\n".join(self.output))
        self.assertTrue(box, "\n".join(self.output))
        return box[0]

    def command(self, cmd, **kw):
        self.p.stdin.write(json.dumps({"cmd":cmd,**kw})+"\n"); self.p.stdin.flush()
        return json.loads(self.read("ACK ").removeprefix("ACK "))

    def post(self, body, *, token=True, path="/api/metadata"):
        headers = {"Content-Type":"application/json"}
        if token: headers["X-Control-Token"]=self.token
        req=urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=5) as response:
                return response.status,json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code,json.loads(error.read())

    def pending(self):
        self.assertEqual(self.post({"action":"acquire"})[1]["state"],"acquiring")
        self.command("finish")
        return self.post({"action":"status"})[1]

    def enable(self):
        self.post({"action":"enable","enabled":True})
        self.command("install")

    def retain_child(self, pid):
        # Creation time may remain queryable on an already-terminated process
        # while Windows retains its object. Hold the exact live process handle
        # and inspect its exit signal, rather than treating that time as alive.
        kernel = ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000,False,pid)  # SYNCHRONIZE; never termination authority.
        self.assertTrue(handle)
        self.addCleanup(kernel.CloseHandle,handle)
        self.assertEqual(kernel.WaitForSingleObject(handle,0),258,"child was not live before release")
        return kernel,handle

    def cleanup(self):
        if self.p.poll() is None:
            try: self.post({},path="/api/system/shutdown")
            except Exception: pass
            try:
                self.p.stdin.write('{"cmd":"stop"}\n'); self.p.stdin.flush()
                self.p.wait(timeout=10)
            except (OSError,subprocess.TimeoutExpired): pass
        self.job.close()
        if self.p.poll() is None: self.p.kill(); self.p.wait(timeout=5)
        for stream in (self.p.stdin,self.p.stdout): stream.close()
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(("127.0.0.1",self.port)),0)

    def test_auth_off_exact_event_draw_conflict_and_no_carryforward(self):
        self.assertEqual(self.post({"action":"enable","enabled":True},token=False)[0],403)
        self.assertEqual(self.post({"action":"acquire"})[1]["state"],"disabled")
        self.assertEqual(self.post({"action":"enable","enabled":1})[0],400)
        self.enable()
        first=self.pending()
        self.command("result",result="win")
        candidate=self.post({"action":"status"})[1]
        self.assertEqual(candidate["request_id"],first["request_id"])
        body={"action":"confirm","request_id":candidate["request_id"],
              "event_id":candidate["event_id"],"same_match":True}
        wrong={**body,"event_id":"wrong"}
        self.assertEqual(self.post(wrong)[1]["state"],"rejected")
        self.assertEqual(self.post(body)[1]["state"],"writing")
        self.command("finish")
        self.assertEqual(self.post({"action":"status"})[1]["last_write"]["state"],"saved")
        self.assertEqual(self.post(body)[1]["state"],"rejected")
        self.command("result",result="loss")
        self.assertNotIn("event_id",self.post({"action":"status"})[1])
        self.pending(); self.command("draw"); self.command("result",result="win")
        self.assertNotIn("event_id",self.post({"action":"status"})[1])
        self.assertEqual(len(self.command("rows")["rows"]),3)

    def test_undo_removal_sidecar_fault_and_optional_lock_never_block_core(self):
        self.enable(); self.pending(); self.command("result",result="loss")
        candidate=self.post({"action":"status"})[1]
        self.assertEqual(self.post({},path="/api/stats/undo")[0],200)
        body={"action":"confirm","request_id":candidate["request_id"],
              "event_id":candidate["event_id"],"same_match":True}
        self.assertEqual(self.post(body)[1]["state"],"rejected")
        self.pending(); self.command("locked")
        self.assertNotIn("event_id",self.post({"action":"status"})[1])
        self.pending(); self.command("result",result="loss")
        candidate=self.post({"action":"status"})[1]
        self.command("sidecar_fault")
        self.post({"action":"confirm","request_id":candidate["request_id"],
                   "event_id":candidate["event_id"],"same_match":True})
        self.command("finish")
        self.assertEqual(len(self.command("rows")["rows"]),2)
        self.assertFalse((self.root/"AC6WinLossTracker"/"enrichment.db").exists())

    def test_actual_server_shutdown_reaps_active_acquisition_before_runtime_removal(self):
        marker=self.root/"native-child"
        script=self.root/"shutdown-worker.py"
        script.write_text(FAKE_WORKER.replace("SOURCE",repr(str(ROOT))).replace("MODE",
            f"Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)"),encoding="utf-8")
        self.post({"action":"enable","enabled":True})
        self.command("native_install",script=str(script))
        self.assertEqual(self.post({"action":"acquire"})[1]["state"],"acquiring")
        deadline=time.monotonic()+10
        while not marker.exists() and time.monotonic()<deadline: threading.Event().wait(.01)
        self.assertTrue(marker.exists())
        pid=int(marker.read_text())
        kernel,handle=self.retain_child(pid)
        self.assertEqual(self.post({},path="/api/system/shutdown")[0],200)
        self.p.stdin.write('{"cmd":"stop"}\n'); self.p.stdin.flush()
        self.read("DONE")
        self.p.wait(timeout=8)
        self.assertEqual(kernel.WaitForSingleObject(handle,0),0,"owned child survived normal server shutdown")
        self.assertFalse((self.root/"AC6WinLossTracker"/".runtime.json").exists())

    def test_gap_ordering_with_authenticated_http_and_actual_owned_child(self):
        # The child's structured frame timestamp is synthetic and the runtime
        # clock deterministic. Actual PID/Job/IPC/HTTP/cleanup remain production.
        self.post({"action":"enable","enabled":True})
        for index, offset in enumerate((-.5, 0.0, 1.0)):
            gap_at = 100.0 + index * 1.5
            captured = gap_at + offset
            marker, release = self.root/f"gap-child-{index}", self.root/f"gap-release-{index}"
            script = self.root/f"gap-worker-{index}.py"
            mode = (f"Path({str(marker)!r}).write_text(str(os.getpid()))\n"
                    f"    while not Path({str(release)!r}).exists(): time.sleep(.01)")
            code = FAKE_WORKER.replace("SOURCE",repr(str(ROOT))).replace("MODE",mode)
            self.assertIn('"captured_at":time.monotonic()',code)
            code = code.replace('"captured_at":time.monotonic()',f'"captured_at":{captured!r}')
            script.write_text(code,encoding="utf-8")
            self.command("native_install",script=str(script),fake_clock=True)
            self.assertEqual(self.post({"action":"acquire"})[1]["state"],"acquiring")
            deadline = time.monotonic()+10
            while not marker.exists() and time.monotonic()<deadline: threading.Event().wait(.01)
            self.assertTrue(marker.exists())
            pid = int(marker.read_text())
            kernel,handle = self.retain_child(pid)
            self.command("gap")  # Cleanup time alone cannot prove capture after this gap.
            self.assertEqual(self.post({"action":"status"})[1]["state"],"acquiring")
            self.command("expire",seconds=1.5)
            release.write_text("GO",encoding="utf-8")
            self.command("finish")
            self.assertEqual(kernel.WaitForSingleObject(handle,0),0,"gap acquisition left its owned child")
            status = self.post({"action":"status"})[1]
            if captured <= gap_at:
                self.assertEqual((status["state"],status["reason"]),("unknown","capture_gap"))
            else:
                self.assertEqual(status["state"],"pending")
                self.command("result",result="win")
                self.assertEqual(self.post({"action":"status"})[1]["state"],"confirmation")
                self.command("gap")
                self.assertNotIn("event_id",self.post({"action":"status"})[1])
                self.command("result",result="loss")
                self.assertNotIn("event_id",self.post({"action":"status"})[1])
            self.assertFalse((self.root/"AC6WinLossTracker"/"enrichment.db").exists())
        self.assertEqual(len(self.command("rows")["rows"]),2)

    def test_diagnostics_off_and_during_actual_owned_metadata_child(self):
        # Real server/HTTP, real contained child and real asynchronous ZIP writer;
        # classifier/capture inputs alone are synthetic, never a game/T3 claim.
        off = self.command("diagnostic_composition")
        self.assertEqual(off["frames"], ["CLEAR", "FINAL_WIN", "CLEAR"])
        self.assertTrue(off["writer_dead"])
        self.assertEqual(self.post({"action":"status"})[1]["state"], "disabled")
        self.assertFalse((self.root/"AC6WinLossTracker"/"enrichment.db").exists())
        # Preserve first completed evidence while giving the ON phase its own ring.
        for bundle in (self.root/"AC6WinLossTracker"/"diagnostics"/"candidate-bundles").glob("*.zip"):
            bundle.rename(bundle.with_suffix(".preserved"))
        self.post({"action":"enable","enabled":True})
        marker, release = self.root/"composition-child", self.root/"composition-release"
        script = self.root/"composition-worker.py"
        mode = (f"Path({str(marker)!r}).write_text(str(os.getpid()))\n"
                f"    while not Path({str(release)!r}).exists(): time.sleep(.01)")
        script.write_text(FAKE_WORKER.replace("SOURCE",repr(str(ROOT))).replace("MODE",mode),encoding="utf-8")
        self.command("native_install",script=str(script))
        self.assertEqual(self.post({"action":"acquire"})[1]["state"],"acquiring")
        deadline = time.monotonic()+10
        while not marker.exists() and time.monotonic()<deadline: threading.Event().wait(.01)
        self.assertTrue(marker.exists())
        kernel,handle = self.retain_child(int(marker.read_text()))
        try:
            on = self.command("diagnostic_composition")
            self.assertEqual(on["frames"],off["frames"])
            self.assertTrue(on["writer_dead"])
            self.assertEqual(kernel.WaitForSingleObject(handle,0),258)
            self.assertEqual(self.post({"action":"status"})[1]["state"],"acquiring")
            self.post({"action":"cancel"})
        finally:
            release.write_text("GO",encoding="utf-8")
            self.command("finish")
        self.assertEqual(kernel.WaitForSingleObject(handle,0),0)
        self.assertNotIn("event_id",self.post({"action":"status"})[1])
        self.assertEqual(self.command("rows")["rows"],[])
        self.assertFalse((self.root/"AC6WinLossTracker"/"enrichment.db").exists())


@unittest.skipUnless(os.name=="nt","Windows contained process")
class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="ac6-lobby-owned-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def script(self, mode="pass"):
        path=self.root/"worker.py"
        path.write_text(FAKE_WORKER.replace("SOURCE",repr(str(ROOT))).replace("MODE",mode),encoding="utf-8")
        return path
    def test_actual_owned_child_success_crash_cancel_timeout_and_repeated_cleanup(self):
        cancel=threading.Event()
        for mode in ("pass", "raise RuntimeError('crash')", "time.sleep(30)", "pass"):
            result=acquire(TARGET,cancel,script=self.script(mode))
            self.assertTrue(result["cleaned"],result)
            if mode=="pass": self.assertEqual(result["status"],"recognized")
            else: self.assertEqual(result["status"],"failed")
        cancel.set()
        self.assertTrue(acquire(TARGET,cancel,script=self.script())["cleaned"])
    def test_job_assignment_failure_never_releases_ready(self):
        marker=self.root/"native_started"
        script=self.script(f"Path({str(marker)!r}).write_text('BAD')")
        with patch("lobby_capture.KillOnCloseJob",side_effect=OSError("no containment")):
            result=acquire(TARGET,threading.Event(),script=script)
        self.assertTrue(result["cleaned"])
        self.assertFalse(marker.exists())

    def test_valid_response_followed_by_crash_or_hang_never_becomes_pending(self):
        for after_response in ("os._exit(23)", "time.sleep(30)"):
            script = self.script()
            code = script.read_text(encoding="utf-8")
            seam='try: owned_entry(ready, work, (), owner=owner)'
            self.assertEqual(code.count(seam),1)
            code=code.replace(seam,'    '+after_response+'\n'+seam)
            script.write_text(code,encoding="utf-8")
            runtime=MetadataRuntime(self.root,enabled=lambda:True,target=lambda:TARGET,
                worker=lambda t,c:acquire(t,c,script=script))
            runtime.acquire()
            task=runtime.task
            if task: task.join(8); self.assertFalse(task.is_alive())
            self.assertIsNone(runtime.pending)
            self.assertEqual(runtime.status()["state"],"unknown")
            runtime.shutdown()

    def wait_file(self, path):
        deadline = time.monotonic() + 10
        while not path.exists() and time.monotonic() < deadline:
            threading.Event().wait(.01)
        self.assertTrue(path.exists(), "owned lifecycle marker missing")

    def test_real_worker_cancel_off_and_runtime_shutdown_are_reaped(self):
        for action in ("cancel", "off", "shutdown"):
            marker = self.root / action
            script = self.script(f"Path({str(marker)!r}).write_text('started'); time.sleep(30)")
            enabled = [True]
            processes = []
            def spawn(*args, **kwargs):
                p = spawn_python(*args, **kwargs); processes.append(p); return p
            runtime = MetadataRuntime(self.root, enabled=lambda: enabled[0], target=lambda: TARGET,
                worker=lambda target,cancel: acquire(target,cancel,script=script))
            with patch("lobby_capture.spawn_python",side_effect=spawn):
                runtime.acquire(); self.wait_file(marker)
                self.assertEqual(runtime.acquire()["state"],"busy")
                if action == "off": enabled[0] = False; runtime.status()
                elif action == "shutdown": runtime.shutdown()
                else: runtime.cancel()
                task = runtime.task
                if task: task.join(8); self.assertFalse(task.is_alive())
            self.assertEqual(len(processes),1)
            self.assertIsNotNone(processes[0].poll())
            self.assertIsNone(runtime.pending)
            self.assertIsNone(runtime.candidate)
            runtime.shutdown()

    def test_parent_death_before_go_and_contained_descendant_after_go(self):
        # No outer job owns these descendants: this proves the production parent
        # witness before GO and its own KillOnCloseJob after GO, separately.
        import lobby_capture
        for released in (False,True):
            child_marker=self.root/f"child-{released}"
            grand_marker=self.root/f"grand-{released}"
            ready_marker=self.root/f"ready-{released}"
            child=self.script(f"import subprocess; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); Path({str(grand_marker)!r}).write_text(str(p.pid)); time.sleep(30)")
            owner_code = '''
import json,os,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from lobby_capture import process_birth,PipeReader
from python_spawn import spawn_python
from owned_worker import KillOnCloseJob
import subprocess
p=spawn_python([sys.argv[2],str(os.getpid()),str(process_birth(os.getpid()))],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,creationflags=subprocess.CREATE_NO_WINDOW)
Path(sys.argv[3]).write_text(str(p.pid))
reader=PipeReader(p.stdout)
while reader.read() is None: time.sleep(.01)
job=KillOnCloseJob(p.pid) if sys.argv[6]=='True' else None
if job:
 p.stdin.write((json.dumps({'go':True,'target':{},'deadline':time.monotonic()+5})+'\\n').encode()); p.stdin.flush()
Path(sys.argv[5]).write_text('ready')
time.sleep(30)
'''
            owner=spawn_python(["-c",owner_code,str(ROOT),str(child),str(child_marker),
                                str(grand_marker),str(ready_marker),str(released)],
                                creationflags=subprocess.CREATE_NO_WINDOW)
            handles=[]
            try:
                self.wait_file(child_marker); self.wait_file(ready_marker)
                pids=[int(child_marker.read_text())]
                if released:
                    self.wait_file(grand_marker); pids.append(int(grand_marker.read_text()))
                kernel=ctypes.WinDLL("kernel32",use_last_error=True)
                kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
                kernel.OpenProcess.restype=wintypes.HANDLE
                kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
                kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]
                kernel.CloseHandle.argtypes=[wintypes.HANDLE]
                for pid in pids:
                    handle=kernel.OpenProcess(0x100001,False,pid)
                    self.assertTrue(handle); handles.append(handle)
                owner.kill(); owner.wait(timeout=5)
                for handle in handles:
                    self.assertEqual(kernel.WaitForSingleObject(handle,8000),0,"owned descendant survived")
                if not released: self.assertFalse(grand_marker.exists())
            finally:
                if owner.poll() is None: owner.kill(); owner.wait(timeout=5)
                for handle in handles:
                    if kernel.WaitForSingleObject(handle,0)!=0:
                        kernel.TerminateProcess(handle,1); kernel.WaitForSingleObject(handle,5000)
                    kernel.CloseHandle(handle)

    def test_malformed_partial_oversized_and_wrong_nonce_ipc_reap(self):
        for payload in ("print('not-json',flush=True)",
                        "print('x'*4097,flush=True)",
                        "print('{}',flush=True)",
                        "sys.stdout.write('{'); sys.stdout.flush(); time.sleep(30)"):
            script=self.root/"bad-worker.py"
            script.write_text("import sys,time\n"+payload,encoding="utf-8")
            result=acquire(TARGET,threading.Event(),script=script)
            self.assertEqual(result["status"],"failed")
            self.assertTrue(result["cleaned"])


if __name__=="__main__": unittest.main(verbosity=2)

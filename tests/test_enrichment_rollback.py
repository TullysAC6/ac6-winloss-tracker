"""T2: exact-base new -> unaware old -> new, real isolated server phases."""
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rollback_previous_version import PREVIOUS_VERSION, extract_previous, assert_listener_released
from t1.process import WorkerJob
from python_spawn import spawn_python

PHASE = r'''
import builtins, contextlib, hashlib, json, os, sqlite3, sys, threading, time, urllib.request, urllib.error
from pathlib import Path
from unittest.mock import patch
source, root, phase, scenario = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
assert sys.stdin.readline().strip() == "GO", "parent did not install ownership"
sys.path.insert(0, str(source))
data = root / "AC6WinLossTracker"
if phase == "crash":
    connection = sqlite3.connect(data / "enrichment.db")
    connection.execute("PRAGMA cache_size=1")
    connection.execute("PRAGMA cache_spill=1")
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("UPDATE match_bindings SET parent_result='loss'")
    connection.execute("UPDATE maintenance_state SET after_event_id='C'")
    journal = data / "enrichment.db-journal"
    assert journal.exists() and journal.read_bytes()[:8] != bytes(8), "no real hot journal"
    print("owned SQLite crash with hot rollback journal", flush=True)
    os._exit(23)
def rows():
    connection = sqlite3.connect((data / "history.db").as_uri() + "?mode=ro", uri=True)
    try:
        return connection.execute("SELECT event_id,created_at,result FROM matches ORDER BY event_id").fetchall()
    finally:
        connection.close()
def guard(original):
    def checked(path, *args, **kwargs):
        assert "enrichment.db" not in str(path), "dormant runtime accessed sidecar"
        return original(path, *args, **kwargs)
    return checked
@contextlib.contextmanager
def dormant():
    with patch.object(Path, "stat", guard(Path.stat)), patch.object(sqlite3, "connect", guard(sqlite3.connect)), \
            patch.object(builtins, "open", guard(builtins.open)):
        yield
def relax_enrichment_budget():
    # Test-only: shared CI runners cannot guarantee the real 100 ms cooperative
    # deadline for fixture setup that is not itself measuring timing. Give this
    # child process deterministic headroom instead; production and the explicit
    # forced-expiry coverage in test_enrichment_store.py are both untouched.
    import enrichment_store
    assert enrichment_store._BUDGET == 0.100
    patch.object(enrichment_store, "_BUDGET", 2.0).start()
if phase == "seed":
    from history_store import HistoryStore
    store = HistoryStore(data)
    store.start_session(started_at=1)
    for key, timestamp in (("A",10), ("B",20), ("X",25)):
        store.record_result(key, "win", "fixture", {}, created_at=timestamp)
    store.close_session(ended_at=26)
    store.start_session(started_at=27)
    store.record_result("C", "loss", "fixture", {}, created_at=30)
    store.close_session(ended_at=31)
ready, failure = threading.Event(), []
with dormant():
    import server
    def run():
        try:
            server.main(on_ready=ready.set)
        except BaseException as error:
            failure.append(f"{type(error).__name__}: {error}")
            ready.set()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert ready.wait(40) and not failure, failure
    assert "optional_enrichment" not in sys.modules and "enrichment_store" not in sys.modules
config = json.loads((data / "config.json").read_text(encoding="utf-8"))
def post(path, payload):
    request = urllib.request.Request(f"http://127.0.0.1:{config['port']}" + path,
        data=json.dumps(payload).encode(), headers={"Content-Type":"application/json", "X-Control-Token":server.CONTROL_TOKEN})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())
state = {"pid":os.getpid(), "server_file":str(Path(server.__file__).resolve()),
         "server_sha256":hashlib.sha256(Path(server.__file__).read_bytes()).hexdigest(), "before":rows()}
try:
    if phase == "seed":
        relax_enrichment_budget()
        assert not (data / "enrichment.db").exists()
        from optional_enrichment import OptionalEnrichmentService
        service = OptionalEnrichmentService(data, active=True)
        for key in ("A","B","C"):
            result = service.save_binding(service.prepare_binding(key).ticket, create_missing=True)
            assert result.status == "saved", result
        assert len(service.lookup_many(["A","B","C"]).bindings) == 3
    elif phase == "old":
        assert not (source / "optional_enrichment.py").exists()
        with dormant():
            if scenario == "undo":
                before = rows()
                server.undo_result()
                assert rows() == before, "old Undo unexpectedly removed closed-session C"
            if scenario in ("record", "undo", "refuse"):
                if scenario == "refuse":
                    cutoff = server.latest_allowed_cutoff(time.time())
                    with patch.object(server.time, "time", return_value=cutoff-1):
                        assert server.record_result("win", "manual")
                else:
                    assert server.record_result("win", "manual")
                state["D"] = list(set(r[0] for r in rows()) - set(r[0] for r in state["before"]))[0]
            if scenario == "undo":
                server.undo_result()
            elif scenario == "date":
                status, result = post("/api/history/purge", {"mode":"before", "cutoff":25})
                assert status == 200, (status,result)
            elif scenario == "full":
                status, result = post("/api/history/purge", {"mode":"all"})
                assert status == 200, (status,result)
                time.sleep(5.05)  # Respect the real post-purge result cooldown; no gate override.
                assert server.record_result("loss", "manual")
                state["D"] = rows()[0][0]
            elif scenario == "refuse":
                before = rows()
                status, result = post("/api/history/purge", {"mode":"before", "cutoff":cutoff})
                assert status == 409 and rows() == before, (status,result)
            elif scenario == "compensate":
                # Primitive across sessions, separately from a real failed old result.
                assert server.history.discard_result("A")
                assert not server.history.discard_result("A")
                before = rows()
                with patch.object(server.stats, "add", side_effect=OSError("controlled stats failure")), \
                        patch.object(server.history, "discard_result", side_effect=OSError("controlled discard failure")):
                    try:
                        server.record_result("win", "manual")
                        raise AssertionError("stats failure was not exercised")
                    except OSError:
                        pass
                assert server.uncounted_event_ids, "durable compensation was not exercised"
                assert server.settle_uncounted_results(server.history)
                assert rows() == before and not server.uncounted_event_ids
    elif phase == "again":
        relax_enrichment_budget()
        from optional_enrichment import OptionalEnrichmentService
        import enrichment_store
        service = OptionalEnrichmentService(data, active=True)
        expected = sorted(set(r[0] for r in rows()) & {"A","B","C"})
        visible = service.lookup_many(["A","B","C"])
        state["visible_before_cleanup"] = sorted(b.event_id for b in visible.bindings)
        assert state["visible_before_cleanup"] == expected, visible
        previous = json.loads((root / "old.json").read_text())
        if "D" in previous:
            assert not service.lookup(previous["D"]).bindings
        # Cleanup failure is independent of real core result/Undo/purge behavior.
        with patch.object(enrichment_store._Store, "remove", side_effect=OSError("injected cleanup fault")):
            assert service.cleanup_step().health.cleanup == "pending"
            with dormant():
                for outcome in ("win","loss"):
                    assert server.record_result(outcome, "manual")
                    server.undo_result()
                    service.invalidate_history()  # test bridge, after the real commit
                assert post("/api/history/purge", {"mode":"before", "cutoff":5})[0] == 200
            service.invalidate_history()
        first = service.cleanup_step()
        assert first.status == "cleaned", first
        assert first.examined <= 64 and first.deleted == 3-len(expected), first
        second = service.cleanup_step()
        assert second.deleted == 0, second
        state["deleted"] = first.deleted
        assert sorted(b.event_id for b in service.lookup_many(["A","B","C"]).bindings) == expected
    elif phase == "bad":
        relax_enrichment_budget()
        from optional_enrichment import OptionalEnrichmentService
        import enrichment_store
        service = OptionalEnrichmentService(data, active=True)
        def core_oracle(action):
            before = rows()
            assert action(), "optional coupling prevented core acceptance"
            assert len(rows()) == len(before)+1
            server.undo_result()
            assert rows() == before
        assert service.lookup("A").status in ("unknown", "unavailable"), service.lookup("A")
        with dormant():
            for outcome in ("win","loss"):
                core_oracle(lambda: server.record_result(outcome, "manual"))
        with patch.object(enrichment_store._Store, "read", side_effect=RuntimeError("optional defect")):
            assert service.lookup("A").status == "unavailable"
            core_oracle(lambda: server.record_result("win", "manual"))
            # Broken test-local coupling exposes the optional exception before core recording.
            def coupled():
                service._store.read(("A",), enrichment_store._Deadline())
                return server.record_result("win", "manual")
            try:
                core_oracle(coupled)
                raise AssertionError("negative control escaped its oracle")
            except RuntimeError as error:
                assert str(error) == "optional defect"
                state["optional_coupling_detected"] = True
    state["after"] = rows()
    (root / (phase + ".json")).write_text(json.dumps(state), encoding="utf-8")
finally:
    assert post("/api/system/shutdown", {})[0] == 200
    worker.join(20)
    assert not worker.is_alive()
    assert not list(data.glob(".*runtime*.json"))
'''


class EnrichmentRollbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ac6-enrichment-t2-")
        cls.previous = extract_previous(Path(cls.directory.name) / "previous")
        if cls.previous is None:
            cls.directory.cleanup()
            raise AssertionError("exact previous source must be available")
        assert PREVIOUS_VERSION == "8d91588bf8057a5756a40cc0d1a98521f7bd00c4"

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def phase(self, source, root, phase, scenario, port, expect_success=True):
        environment = dict(os.environ, LOCALAPPDATA=str(root), PYTHONDONTWRITEBYTECODE="1",
                           PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        environment.pop("PYTHONPATH", None)
        child = spawn_python(["-B", "-c", PHASE, str(source), str(root), phase, scenario],
                                 cwd=source, environment=environment, windowless=False,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 stdin=subprocess.PIPE, text=True,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(f"enrichment phase={phase}/{scenario} pid={child.pid} source={source}", flush=True)
        job = None
        try:
            if os.name == "nt":
                job = WorkerJob()
                job.assign(child.pid)
            stdout, stderr = child.communicate("GO\n", timeout=100)
        except subprocess.TimeoutExpired:
            # Try normal authenticated shutdown first, using only this isolated root.
            runtime = root / "AC6WinLossTracker" / ".runtime.json"
            if runtime.exists():
                import urllib.request
                details = json.loads(runtime.read_text(encoding="utf-8"))
                try:
                    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/system/shutdown", data=b"",
                                                     headers={"X-Control-Token":details.get("token", "")})
                    urllib.request.urlopen(request, timeout=3).close()
                    child.wait(timeout=5)
                except Exception:
                    pass
            if child.poll() is None:
                child.kill()
            stdout, stderr = child.communicate(timeout=5)
            self.fail(f"owned child {child.pid} timed out: {stdout} {stderr}")
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            if job:
                try:
                    remaining = job.wait_empty(2)
                    if remaining:
                        leaked = job.members()
                        job.terminate()
                        self.assertEqual(job.wait_empty(3), 0, leaked)
                        self.fail(f"owned descendants outlived phase: {leaked}")
                finally:
                    job.close()
        assert_listener_released(port)
        if not expect_success:
            self.assertNotEqual(child.returncode, 0)
            return stdout + stderr
        self.assertEqual(child.returncode, 0, stdout + stderr)
        state = json.loads((root / (phase + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(state["server_file"], str((source / "server.py").resolve()))
        self.assertEqual(state["server_sha256"], hashlib.sha256((source / "server.py").read_bytes()).hexdigest())
        self.assertEqual(state["pid"], child.pid)
        self.assertFalse(list((root / "AC6WinLossTracker").glob(".*runtime*.json")))
        return state

    def profile(self, scenario):
        root = Path(tempfile.mkdtemp(prefix=scenario + "-", dir=self.directory.name))
        data = root / "AC6WinLossTracker"
        data.mkdir()
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        self.assertNotEqual(port, 8765)
        (data / "config.json").write_text(json.dumps({"config_version":18, "port":port,
            "result_detector_enabled":False, "effect_screenshot_enabled":False}), encoding="utf-8")
        (data / "preferences.json").write_text(json.dumps({"preferences_version":2,
            "player_streak_status_enabled":False, "broadcast_show_best_streak":False}), encoding="utf-8")
        return root, data, port

    def test_real_previous_deletions_and_reupgrade(self):
        for scenario in ("record", "undo", "date", "full", "refuse", "compensate"):
            with self.subTest(scenario=scenario):
                root, data, port = self.profile(scenario)
                preserved = {name:(data / name).read_bytes() for name in ("config.json", "preferences.json")}
                seed = self.phase(ROOT, root, "seed", scenario, port)
                sidecar = (data / "enrichment.db").read_bytes()
                old = self.phase(self.previous, root, "old", scenario, port)
                self.assertEqual((data / "enrichment.db").read_bytes(), sidecar)
                expected = {"A","B","C","X"}
                if scenario in ("record","refuse"):
                    expected.add(old["D"])
                elif scenario == "date":
                    expected = {"C","X"}
                elif scenario == "full":
                    expected = {old["D"]}
                elif scenario == "compensate":
                    expected.remove("A")
                self.assertEqual({r[0] for r in old["after"]}, expected)
                again = self.phase(ROOT, root, "again", scenario, port)
                self.assertEqual(again["before"], old["after"])
                self.assertEqual(again["after"], old["after"])
                original = {r[0]:r for r in seed["after"]}
                self.assertTrue(all(row == original[row[0]] for row in again["after"] if row[0] in original))
                for name, contents in preserved.items():
                    self.assertEqual((data / name).read_bytes(), contents)

    def test_wrong_core_schema_and_config_negative_controls(self):
        for broken in ("history-v4", "config-key"):
            with self.subTest(broken=broken):
                root, data, port = self.profile(broken)
                self.phase(ROOT, root, "seed", broken, port)
                if broken == "history-v4":
                    with sqlite3.connect(data / "history.db") as connection:
                        connection.execute("PRAGMA user_version=4")
                    connection.close()
                    expected = "ENV-HISTORY-FUTURE-SCHEMA"
                else:
                    config = json.loads((data / "config.json").read_text())
                    config["match_metadata_detection"] = False
                    (data / "config.json").write_text(json.dumps(config), encoding="utf-8")
                    expected = "ENV-CONFIG-INVALID"
                output = self.phase(self.previous, root, "old", broken, port, expect_success=False)
                self.assertIn(expected, output)

    def test_bad_optional_stores_do_not_affect_real_core(self):
        for broken in ("missing", "zero", "corrupt", "future", "locked"):
            with self.subTest(broken=broken):
                root, data, port = self.profile(broken)
                self.phase(ROOT, root, "seed", broken, port)
                optional = data / "enrichment.db"
                lock = None
                if broken == "missing":
                    optional.unlink()
                elif broken == "zero":
                    optional.write_bytes(b"")
                elif broken == "corrupt":
                    optional.write_bytes(b"foreign invalid sqlite")
                elif broken == "future":
                    with sqlite3.connect(optional) as connection:
                        connection.execute("PRAGMA user_version=2")
                    connection.close()
                elif broken == "locked":
                    lock = sqlite3.connect(optional)
                    lock.execute("BEGIN EXCLUSIVE")
                before = optional.read_bytes() if optional.exists() else None
                try:
                    result = self.phase(ROOT, root, "bad", broken, port)
                    self.assertTrue(result["optional_coupling_detected"])
                    self.assertEqual(result["before"], result["after"])
                    self.assertEqual(optional.read_bytes() if optional.exists() else None, before)
                finally:
                    if lock:
                        lock.rollback()
                        lock.close()

    def test_native_journal_recovery_after_owned_process_exit(self):
        from optional_enrichment import OptionalEnrichmentService
        root, data, port = self.profile("journal")
        self.phase(ROOT, root, "seed", "journal", port)
        output = self.phase(ROOT, root, "crash", "journal", port, expect_success=False)
        self.assertIn("owned SQLite crash with hot rollback journal", output)
        service = OptionalEnrichmentService(data, active=True)
        before = (data / "enrichment.db").read_bytes()
        journal = (data / "enrichment.db-journal").read_bytes()
        self.assertEqual(service.inspect().status, "unavailable")
        self.assertEqual((data / "enrichment.db").read_bytes(), before)
        self.assertEqual((data / "enrichment.db-journal").read_bytes(), journal)
        restored = service.save_binding(service.prepare_binding("A").ticket)
        self.assertEqual(restored.status, "saved", restored)
        self.assertEqual({row.event_id for row in service.lookup_many(["A","B","C"]).bindings}, {"A","B","C"})
        self.assertFalse((data / "enrichment.db-journal").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

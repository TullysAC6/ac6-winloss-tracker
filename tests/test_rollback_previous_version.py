"""One-version rollback: the previous build starts on data the new build wrote.

new build: record a result, save the new Player setting
  -> previous build (real source from git): starts, keeps history, records
  -> new build again: starts, keeps both results and the setting

Each phase runs the real server.main in a fresh interpreter with authenticated
normal shutdown, against one isolated LOCALAPPDATA.  A negative control proves
the check is not vacuous: the superseded design (the key inside config.json)
makes the previous build refuse to start.

PREVIOUS_VERSION is the one supported rollback target: the main commit this
generation builds on.  The next generation moves it to its own base.
"""
import ast
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PREVIOUS_VERSION = "89f5f4a386e46081a38d993a535d3f3df4a3ef63"
KEY = "player_streak_status_enabled"

# Runs inside the child with the chosen source tree first on sys.path.
PHASE = r'''
import json, sys, threading, urllib.request
from pathlib import Path
source, root, phase = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, source)
import server
ready, failure = threading.Event(), []
def run():
    try:
        server.main(on_ready=ready.set)
    except BaseException as error:
        failure.append(f"{type(error).__name__}: {error}")
        ready.set()
worker = threading.Thread(target=run, daemon=True)
worker.start()
if not ready.wait(40) or failure:
    print("START FAILED:", failure or "timeout", flush=True)
    sys.exit(2)
data = root / "AC6WinLossTracker"
config = json.loads((data / "config.json").read_text(encoding="utf-8"))
try:
    state = {"lifetime_before": server.history.lifetime_summary()["wins"],
             "server_file": str(Path(server.__file__).resolve())}
    state["recorded"] = bool(server.record_result("win", "manual"))
    state["lifetime_after"] = server.history.lifetime_summary()["wins"]
    if phase == "new-install":
        import preferences
        import settings_window
        # Every boolean preference flipped from its default, so each one the new
        # build adds really lands in preferences.json for the previous build.
        values = {key: not default for key, default in preferences.DEFAULTS.items()
                  if isinstance(default, bool)}
        values["overlay_stats_scope"] = "lifetime"
        settings_window.save_settings(values)
    # Any build that has preferences.py must accept the file with its own
    # reader (from UI-1B on, this exercises the previous build's reader too).
    if (Path(source) / "preferences.py").is_file():
        import settings_window
        state["settings"] = settings_window.read_settings()
    (root / (phase + ".json")).write_text(json.dumps(state), encoding="utf-8")
finally:
    request = urllib.request.Request(
        f"http://127.0.0.1:{config['port']}/api/system/shutdown", data=b"", method="POST",
        headers={"X-Control-Token": server.CONTROL_TOKEN})
    with urllib.request.urlopen(request, timeout=15) as response:
        assert response.status == 200
    worker.join(20)
    assert not worker.is_alive(), "main left running"
    assert not (data / ".runtime.json").exists(), "runtime file left behind"
'''


def extract_previous(destination):
    """The previous build's exact source, from git; fetched if the clone is shallow."""
    git = ["git", "-C", str(ROOT), "-c", f"safe.directory={ROOT}"]
    present = subprocess.run(git + ["cat-file", "-e", PREVIOUS_VERSION + "^{commit}"],
                             capture_output=True, timeout=30).returncode == 0
    if not present:
        shallow = subprocess.run(git + ["rev-parse", "--is-shallow-repository"], capture_output=True,
                                 text=True, timeout=30).stdout.strip() == "true"
        depth = ["--depth=1"] if shallow else []  # never make a full clone shallow
        subprocess.run(git + ["fetch", "--no-tags", *depth, "origin", PREVIOUS_VERSION],
                       capture_output=True, timeout=180)
    archive = subprocess.run(git + ["archive", "--format=tar", PREVIOUS_VERSION],
                             capture_output=True, timeout=120)
    if archive.returncode != 0:
        return None
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
        bundle.extractall(destination, filter="data")
    return destination


def preferences_contract(tree):
    """(PREFERENCES_VERSION, {key: default type name}) from a tree's source, or None."""
    path = Path(tree) / "preferences.py"
    if not path.is_file():
        return None
    version = defaults = None
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None)
        if isinstance(target, ast.Name) and target.id == "PREFERENCES_VERSION":
            version = ast.literal_eval(node.value)
        if isinstance(target, ast.Name) and target.id == "DEFAULTS":
            defaults = {key: type(value).__name__ for key, value in ast.literal_eval(node.value).items()}
    return version, defaults


# UI-1A introduces preferences.json.  It is the only build whose previous build
# has no preferences.py, so a build in that position must be exactly this.
FIRST_GENERATION = (1, {KEY: "bool"})


def contract_violation(previous, new):
    """Why ``new`` breaks one-version rollback to ``previous``, or None."""
    new_version, new_types = new
    if previous is None:
        if new == FIRST_GENERATION:
            return None
        return ("only the first preferences build may follow a build without preferences.py; "
                "move PREVIOUS_VERSION to this generation's base")
    old_version, old_types = previous
    if not set(old_types) <= set(new_types):
        return f"keys removed or renamed: {sorted(set(old_types) - set(new_types))}"
    changed = sorted(key for key in old_types if old_types[key] != new_types[key])
    if changed:
        return f"key types changed (the previous build would reject the file): {changed}"
    if set(new_types) == set(old_types):
        return None if new_version == old_version else "version changed without a key change"
    if new_version != old_version + 1:
        added = sorted(set(new_types) - set(old_types))
        return f"keys {added} added without bumping {old_version} -> {old_version + 1}"
    return None


class PreferencesContractRuleTests(unittest.TestCase):
    """The rule itself, on synthetic contracts; needs no git history."""

    def test_rule(self):
        cases = [
            (None, FIRST_GENERATION, None),
            (None, (2, {KEY: "bool"}), "only the first"),
            (None, (1, {KEY: "bool", "b": "bool"}), "only the first"),  # stale PREVIOUS_VERSION + no bump
            ((1, {"a": "bool"}), (1, {"a": "bool"}), None),
            ((1, {"a": "bool"}), (1, {"a": "bool", "b": "str"}), "without bumping"),
            ((1, {"a": "bool"}), (2, {"a": "bool", "b": "str"}), None),
            ((1, {"a": "bool"}), (3, {"a": "bool", "b": "str"}), "without bumping"),
            ((1, {"a": "bool"}), (2, {"a": "bool"}), "without a key change"),
            ((1, {"a": "bool", "b": "bool"}), (2, {"a": "bool"}), "removed"),
            ((1, {"a": "bool"}), (2, {"a": "str", "b": "bool"}), "types changed"),
        ]
        for previous, new, expected in cases:
            with self.subTest(previous=previous, new=new):
                problem = contract_violation(previous, new)
                if expected is None:
                    self.assertIsNone(problem)
                else:
                    self.assertIn(expected, problem)

    def test_this_tree_contract_is_readable(self):
        import preferences
        self.assertEqual(preferences_contract(ROOT), (
            preferences.PREFERENCES_VERSION,
            {key: type(value).__name__ for key, value in preferences.DEFAULTS.items()}))


def history_rows(data):
    connection = sqlite3.connect(f"file:{data / 'history.db'}?mode=ro", uri=True)
    try:
        return connection.execute("SELECT event_id, result FROM matches ORDER BY id").fetchall()
    finally:
        connection.close()


class RollbackToPreviousVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ac6-rollback-")
        previous = extract_previous(Path(cls.directory.name) / "previous")
        if previous is None:
            cls.directory.cleanup()
            if os.environ.get("CI"):
                raise AssertionError(f"previous version {PREVIOUS_VERSION} is not reachable from CI")
            raise unittest.SkipTest(f"previous version {PREVIOUS_VERSION} is not in this clone")
        cls.previous = previous
        for name in ("app.py", "server.py", "config_utils.py"):
            assert (previous / name).is_file(), name
        assert not (previous / "preferences.py").exists(), "rollback target predates preferences.json"

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="root-", dir=self.directory.name))
        self.data = self.root / "AC6WinLossTracker"
        self.data.mkdir()
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        self.assertNotEqual(self.port, 8765)
        # Exactly the v18 shape both builds accept.
        (self.data / "config.json").write_text(json.dumps({
            "config_version": 18, "port": self.port, "result_detector_enabled": False,
            "effect_screenshot_enabled": False,
        }), encoding="utf-8")

    def phase(self, source, name, expect_start=True):
        env = dict(os.environ, LOCALAPPDATA=str(self.root), PYTHONDONTWRITEBYTECODE="1")
        env.pop("PYTHONPATH", None)
        child = subprocess.run(
            [sys.executable, "-B", "-c", PHASE, str(source), str(self.root), name],
            cwd=source, env=env, capture_output=True, text=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        output = child.stdout + child.stderr
        if not expect_start:
            return child.returncode, output
        self.assertEqual(child.returncode, 0, output)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", self.port))  # listener released
        return json.loads((self.root / (name + ".json")).read_text(encoding="utf-8"))

    def test_preferences_contract_with_the_previous_build(self):
        problem = contract_violation(preferences_contract(self.previous), preferences_contract(ROOT))
        self.assertIsNone(problem, "one-version rollback contract broken")

    def test_new_then_save_setting_then_previous_starts_then_new_again(self):
        installed = self.phase(ROOT, "new-install")
        self.assertTrue(Path(installed["server_file"]).is_relative_to(ROOT.resolve()))
        self.assertEqual((installed["lifetime_before"], installed["lifetime_after"]), (0, 1))
        self.assertIs(installed["settings"][KEY], False)
        self.assertEqual(installed["settings"]["overlay_stats_scope"], "lifetime")
        config_after_new = (self.data / "config.json").read_bytes()
        preferences_after_new = (self.data / "preferences.json").read_bytes()
        self.assertNotIn(KEY.encode(), config_after_new, "the new setting never enters config.json")
        rows_after_new = history_rows(self.data)
        self.assertEqual(len(rows_after_new), 1)

        rolled_back = self.phase(self.previous, "previous-start")
        self.assertTrue(Path(rolled_back["server_file"]).is_relative_to(self.previous.resolve()),
                        "the previous build's own server.py ran")
        previous_contract = preferences_contract(self.previous)
        if previous_contract is not None:
            self.assertIn("settings", rolled_back, "the previous build read preferences.json itself")
            for key, type_name in previous_contract[1].items():
                if type_name == "bool":  # new-install saved every bool flipped from its default
                    self.assertIs(rolled_back["settings"][key], installed["settings"][key], key)
        self.assertEqual((rolled_back["lifetime_before"], rolled_back["recorded"],
                          rolled_back["lifetime_after"]), (1, True, 2))
        self.assertEqual((self.data / "config.json").read_bytes(), config_after_new,
                         "the previous build accepted config.json as-is")
        self.assertEqual((self.data / "preferences.json").read_bytes(), preferences_after_new,
                         "the previous build leaves preferences.json alone")
        self.assertEqual(history_rows(self.data)[:1], rows_after_new, "history preserved")

        upgraded = self.phase(ROOT, "new-again")
        self.assertEqual((upgraded["lifetime_before"], upgraded["lifetime_after"]), (2, 3))
        self.assertIs(upgraded["settings"][KEY], False, "the saved setting survives the round trip")
        self.assertEqual(upgraded["settings"]["overlay_stats_scope"], "lifetime")
        self.assertEqual(len(history_rows(self.data)), 3)
        self.assertEqual(list(self.data.glob(".*runtime*.json")), [])

    def test_negative_control_the_superseded_design_breaks_the_previous_build(self):
        config = json.loads((self.data / "config.json").read_text(encoding="utf-8"))
        config[KEY] = False  # what the superseded candidate f2e4b4d wrote
        (self.data / "config.json").write_text(json.dumps(config), encoding="utf-8")
        before = (self.data / "config.json").read_bytes()
        returncode, output = self.phase(self.previous, "previous-refuses", expect_start=False)
        self.assertNotEqual(returncode, 0, output)
        # Same config as the positive test except the key: the previous build
        # fails closed at its config gate, before binding the port.
        self.assertIn("ENV-CONFIG-INVALID", output)
        self.assertEqual((self.data / "config.json").read_bytes(), before, "and changed nothing")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", self.port))


if __name__ == "__main__":
    unittest.main(verbosity=2)

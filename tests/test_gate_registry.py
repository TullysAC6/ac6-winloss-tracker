"""T0: every test is owned by exactly one gate, and CI runs the gates in order.

The registry is tests/gate_registry.py. A new test file that is not registered,
a test claimed by two gates, a selector naming nothing, or a CI workflow that
runs a gate out of order all fail here.
"""
import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import gate_registry  # noqa: E402
from gate_registry import ENTRIES, GATES, T0, T1, T2  # noqa: E402


def discovered_entry_points():
    found = {path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").glob("test_*.py")}
    found |= {path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").glob("t2_*.py")}
    found |= {path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").glob("test_*.ps1")}
    found |= {path.name for path in ROOT.glob("test_*.py")}
    found.add("tests/run_t1.py")
    return found


def unittest_tests(path):
    """{class: set(test methods)} including tests inherited from classes in the same file."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}

    def methods(name, seen=()):
        node = classes[name]
        own = {item.name for item in node.body if isinstance(item, ast.FunctionDef) and item.name.startswith("test")}
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in classes and base.id not in seen:
                own |= methods(base.id, seen + (name,))
        return own

    return {name: tests for name in classes if (tests := methods(name))}


class GateRegistryTests(unittest.TestCase):
    def test_every_test_entry_point_is_registered(self):
        registered = {entry.path for entry in ENTRIES}
        self.assertEqual(sorted(discovered_entry_points() - registered), [], "unregistered test entry point")
        self.assertEqual(sorted(registered - discovered_entry_points()), [], "registered file does not exist")

    def test_entries_are_well_formed(self):
        for entry in ENTRIES:
            with self.subTest(entry.path):
                self.assertIn(entry.gate, GATES)
                self.assertTrue(entry.reason.strip())
                suffix = Path(entry.path).suffix
                if entry.gate == T1:
                    self.assertEqual((entry.path, entry.runner), ("tests/run_t1.py", "t1"))
                elif suffix == ".ps1":
                    self.assertEqual(entry.runner, "powershell-ci")
                else:
                    self.assertEqual(entry.runner, "python")
        self.assertEqual([entry.path for entry in ENTRIES if entry.gate == T1], ["tests/run_t1.py"])

    def test_every_test_is_owned_exactly_once(self):
        by_path = {}
        for entry in ENTRIES:
            by_path.setdefault(entry.path, []).append(entry)
        for path, owners in by_path.items():
            with self.subTest(path):
                whole = [entry for entry in owners if not entry.selectors]
                if whole:
                    self.assertEqual(len(owners), 1, "a whole-file owner cannot share the file")
                    continue
                tests = unittest_tests(path)
                self.assertTrue(tests, "selectors need unittest classes")
                source = (ROOT / path).read_text(encoding="utf-8")
                self.assertRegex(source, r"unittest\.main\(", "selectors are passed to the file's unittest.main()")
                owned = {}
                for entry in owners:
                    for selector in entry.selectors:
                        cls, _, method = selector.partition(".")
                        self.assertIn(cls, tests, f"{selector} names no test class")
                        names = {method} if method else tests[cls]
                        if method:
                            self.assertIn(method, tests[cls], f"{selector} names no test")
                        for name in names:
                            key = f"{cls}.{name}"
                            self.assertNotIn(key, owned, f"{key} is owned by {owned.get(key)} and {entry.gate}")
                            owned[key] = entry.gate
                expected = {f"{cls}.{name}" for cls, names in tests.items() for name in names}
                self.assertEqual(sorted(expected - set(owned)), [], "tests without a gate")

    def test_the_pre_14_regression_suite_is_all_still_run(self):
        pre_14 = {
            "test_game_capture.py", "test_effect_screenshot.py", "test_windows_capture_integration.py",
            "test_native_effect_screenshot.py", "test_occlusion_cloaked.py", "test_app_overlay_dispatch.py",
            "test_overlay_lifecycle.py", "test_launcher.py", "test_launcher_gui_lifecycle.py",
            "test_settings_window.py", "test_shutdown.py", "test_profile_optimization.py", "test_detector.py",
            "test_state_machine.py", "test_stats_manager.py", "test_event_bus.py", "test_issue4_effect_replay.py",
            "test_resource_optimization.py", "test_diagnostic_report.py", "test_result_gate.py", "test_config.py",
            "test_settings_actions.py", "test_purge_atomicity.py", "test_purge_history_writability.py",
            "test_result_persistence_invariant.py", "test_pending_history_recovery.py", "test_test_isolation.py",
            "test_t2_harness_selfcheck.py", "test_history_store.py", "test_history_analytics.py",
            "test_dashboard_runtime.py", "test_dashboard_server.py", "test_dashboard_history_view.py",
            "test_dashboard_static.py", "test_overlay_static.py", "test_server_static.py",
            "test_stable_distribution_static.py", "test_readme_bootstrap_hash.py", "test_startup_preflight.py",
        }
        pre_14 = {f"tests/{name}" for name in pre_14} | {
            "test_strict_clear_gate.py", "test_game_overlay_static.py", "test_game_overlay_lifecycle_static.py"}
        self.assertEqual(len(pre_14), 42)
        ci_python = {entry.path for entry in ENTRIES if entry.runner == "python" and entry.ci}
        self.assertEqual(sorted(pre_14 - ci_python), [], "a pre-#14 regression file is no longer run in CI")

    def test_runners_execute_only_their_own_gate(self):
        t0 = (ROOT / "tests" / "run_all_tests.py").read_text(encoding="utf-8")
        t2 = (ROOT / "tests" / "run_t2.py").read_text(encoding="utf-8")
        self.assertIn("python_commands(T0)", t0)
        self.assertIn("python_commands(T2, ci_only=arguments.ci)", t2)
        self.assertNotRegex(t0, r"files\s*=\s*\[", "the T0 runner must not keep its own file list")
        for gate in (T0, T2):
            for label, command in gate_registry.python_commands(gate):
                self.assertTrue((ROOT / command[0]).is_file(), label)

    def test_ci_runs_t0_then_t1_then_t2(self):
        workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        steps = [(match.start(), match.group(0)) for match in re.finditer(
            r"python tests/run_all_tests\.py|python tests/run_t1\.py|python tests/run_t2\.py --ci|"
            r"tests/test_source_install_flow\.ps1", workflow)]
        self.assertEqual([name for _, name in steps],
                         ["python tests/run_all_tests.py", "python tests/run_t1.py", "python tests/run_t2.py --ci",
                          "tests/test_source_install_flow.ps1"])
        self.assertNotIn("python tests/test_launcher.py", workflow, "the launcher tests run once, inside T2")
        for command in ("run_all_tests.py", "run_t1.py", "run_t2.py --ci"):
            block = workflow[:workflow.index(f"python tests/{command}")]
            step = block[block.rindex("- name:"):]
            self.assertIn("matrix.python-version != '3.12'", step, f"{command} runs on 3.13 and 3.14 only")


if __name__ == "__main__":
    unittest.main(verbosity=2)

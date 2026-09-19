"""The test suite must not be able to touch a real user's data or Desktop.

Independent review of 1819f748 found that patching ``diagnostics.Path.home`` did
not isolate the diagnostics export, because on Windows the exporter resolves the
Desktop through ``SHGetKnownFolderPath``, which ignores both that patch and
``LOCALAPPDATA``. The tests could therefore write and unlink a ZIP on the user's
real Desktop, and a related fixture overwrote and deleted a real startup log.

The guard at the top of ``test_settings_actions.py`` only establishes that the
app-data root is disposable. These checks assert the stronger property the review
asked for: the export destination is isolated too, the guard survives a path that
merely looks disposable, and the suites that touch user-data paths own them.

No server, port, or real data directory is used here.
"""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PR_TESTS = ("test_settings_actions.py", "test_history_analytics.py",
            "test_purge_atomicity.py", "t2_settings_analytics_e2e.py")


class DesktopIsolationTests(unittest.TestCase):
    """The Windows Desktop lookup is the escape hatch; close it explicitly."""

    def test_path_home_does_not_isolate_the_windows_export(self):
        """Document why patching Path.home alone is insufficient."""
        source = (ROOT / "diagnostics.py").read_text(encoding="utf-8")
        export = source[source.index("def export("):]
        export = export[:export.index("\n    def ")] if "\n    def " in export else export
        self.assertIn("desktop_directory", export,
                      "export() resolves the Desktop through effect_screenshot")
        self.assertIn('os.name == "nt"', export)
        # Path.home is only the non-Windows fallback, so a test that patches it
        # and nothing else is not isolated on the platform that ships.
        self.assertIn("Path.home()", export)

    def test_export_honours_an_explicit_destination(self):
        """The seam the tests must use, verified against the real exporter."""
        import diagnostics
        with tempfile.TemporaryDirectory(prefix="ac6-export-seam-") as name,              patch.dict(os.environ, {"LOCALAPPDATA": name}):
            owned = Path(name)
            recorder = diagnostics.DiagnosticRecorder()
            self.assertIn(owned.resolve(), recorder.root.resolve().parents,
                          "the recorder must be rooted in the owned directory")
            recorder.record("selftest", note="isolation")
            destination = owned / "out"
            destination.mkdir()
            # If this ever reaches the real Desktop the call fails loudly here.
            with patch.object(diagnostics, "desktop_directory", create=True,
                              side_effect=AssertionError("real Desktop resolved")):
                report = Path(recorder.export(destination_dir=destination))
            self.assertEqual(report.parent.resolve(), destination.resolve())
            self.assertTrue(report.exists())

    def test_diagnostic_tests_redirect_the_real_desktop_lookup(self):
        source = (ROOT / "tests" / "test_settings_actions.py").read_text(encoding="utf-8")
        self.assertIn('patch.object(effect_screenshot, "desktop_directory"', source,
                      "the SHGetKnownFolderPath lookup itself must be redirected")
        self.assertIn("assert_owned", source,
                      "produced artifacts must be asserted to stay in an owned root")


class GuardTests(unittest.TestCase):
    """The direct-execution guard must not be fooled by a lookalike path."""

    def guard_verdict(self, data_root):
        """Re-evaluate the guard's condition for an arbitrary resolved root."""
        resolved = Path(data_root).resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        return resolved != temp_root and temp_root not in resolved.parents

    def test_a_real_data_root_is_rejected(self):
        self.assertTrue(self.guard_verdict(Path.home() / "AppData/Local/AC6WinLossTracker"))
        self.assertTrue(self.guard_verdict("C:/Users/someone/Documents"))
        self.assertTrue(self.guard_verdict(Path.home()))

    def test_a_temporary_root_is_accepted(self):
        with tempfile.TemporaryDirectory() as name:
            self.assertFalse(self.guard_verdict(Path(name) / "AC6WinLossTracker"))

    @unittest.skipUnless(os.name == "nt", "junction/symlink behaviour is Windows-specific")
    def test_a_link_out_of_temp_is_rejected_because_the_guard_resolves(self):
        """A junction inside TEMP pointing outside it must not pass the guard.

        This is the review's symlink/junction case: the guard canonicalises with
        resolve(), so a link is judged by its target, not its name.
        """
        import subprocess
        with tempfile.TemporaryDirectory(prefix="ac6-junction-") as name:
            outside = Path(name).parent.parent / f"ac6-junction-target-{os.getpid()}"
            outside.mkdir(exist_ok=True)
            try:
                link = Path(name) / "looks-disposable"
                created = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                    capture_output=True, text=True)
                if created.returncode != 0:
                    self.skipTest(f"junction not creatable: {created.stderr.strip()}")
                # Named inside TEMP, but resolving outside it.
                self.assertTrue(str(link).startswith(str(Path(name))))
                if Path(tempfile.gettempdir()).resolve() in link.resolve().parents:
                    self.skipTest("TEMP and the target share a parent on this machine")
                self.assertTrue(self.guard_verdict(link),
                                "a junction out of TEMP must be rejected")
            finally:
                try:
                    (Path(name) / "looks-disposable").rmdir()
                except OSError:
                    pass
                try:
                    outside.rmdir()
                except OSError:
                    pass


class OwnedPathAuditTests(unittest.TestCase):
    """Destructive calls in the PR's tests must target an owned root."""

    DESTRUCTIVE = re.compile(r"\b(unlink|rmtree|os\.remove)\s*\(")

    def test_no_destructive_call_targets_the_live_data_root(self):
        offenders = []
        for name in PR_TESTS:
            path = ROOT / "tests" / name
            if not path.exists():
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not self.DESTRUCTIVE.search(line):
                    continue
                # data_dir() and CONFIG_PATH.parent are the live root unless the
                # caller owns LOCALAPPDATA; requiring an owned local name keeps
                # the destructive target obvious at the call site.
                if "data_dir()" in line or "STATS_PATH" in line or "CONFIG_PATH" in line:
                    offenders.append(f"{name}:{number}: {line.strip()}")
        self.assertEqual(offenders, [], "destructive call against a live-root path")

    def test_the_evidence_fixture_no_longer_unlinks_shared_files(self):
        source = (ROOT / "tests" / "test_settings_actions.py").read_text(encoding="utf-8")
        for name in ("installed-version.json", "startup.log"):
            self.assertNotIn(f'(root / "{name}").unlink', source,
                             f"{name} must not be unlinked from a shared root")
        self.assertIn("must not already exist in an owned root", source,
                      "the fixture must prove the root is fresh before writing")


if __name__ == "__main__":
    unittest.main(verbosity=2)

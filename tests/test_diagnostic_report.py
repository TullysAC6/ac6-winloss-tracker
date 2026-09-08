import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class DiagnosticReportTests(unittest.TestCase):
    def test_journal_failure_does_not_drop_health_event(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}):
            import server
            for method in ('record', 'flush_frame_context'):
                recorder = Mock()
                getattr(recorder, method).side_effect = OSError('disk full')
                with patch.object(server, 'RECORDER', recorder), patch.object(server, 'event_bus') as bus:
                    server.publish('detector', {'status': 'waiting'}, remember=False)
                    bus.publish.assert_called_once_with('detector', {'status': 'waiting'}, remember=False)

    def test_capture_transition_survives_offline_export(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}):
            import diagnostics
            import server
            recorder = diagnostics.DiagnosticRecorder()
            recorder.buffer_frame(frame_state='FINAL_WIN', state_before={'last_reject_reason': 'not_armed'})
            with patch.object(server, 'RECORDER', recorder):
                server.publish('detector', {'status': 'waiting', 'error': 'WGC worker timed out'}, remember=False)
            # The report helper is another process/recorder with an empty ring.
            offline = diagnostics.DiagnosticRecorder()
            archive = offline.export(destination_dir=directory)
            with zipfile.ZipFile(archive) as bundle:
                log = bundle.read('detector.jsonl')
                self.assertIn(b'WGC worker timed out', log)
                self.assertIn(b'not_armed', log)
                self.assertIn(b'capture_waiting', log)

    @unittest.skipUnless(os.name == 'nt', 'Windows Known Folder')
    def test_redirected_desktop_and_api_failure_fallback(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}):
            import diagnostics
            recorder = diagnostics.DiagnosticRecorder()
            desktop = Path(directory) / 'OneDrive-日本語-Desktop'
            desktop.mkdir()
            with patch('effect_screenshot.desktop_directory', return_value=desktop):
                self.assertEqual(recorder.export().parent, desktop)
            with patch('effect_screenshot.desktop_directory', side_effect=OSError('unavailable')):
                self.assertEqual(recorder.export().parent, Path(directory) / 'AC6WinLossTracker')

    def test_report_retains_capture_and_install_evidence(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}):
            import diagnostics
            recorder = diagnostics.DiagnosticRecorder()
            data = Path(directory) / "AC6WinLossTracker"
            expected = ("startup.log", "source-install.log", "installed-version.json")
            for name in expected:
                (data / name).write_text('{}', encoding='utf-8')
            (data / '.runtime.json').write_text('{"token":"do-not-export"}', encoding='utf-8')
            for name in ('effect-screenshot.jsonl', 'effect-screenshot.jsonl.1'):
                (recorder.root / name).write_text('{"reason":"occluded"}\n', encoding='utf-8')
            recorder.record('detector_health', state={'status': 'waiting', 'error': 'WGC unavailable'})
            with patch('diagnostics.Path.home', return_value=Path(directory)):
                archive = recorder.export(destination_dir=directory)
            with zipfile.ZipFile(archive) as bundle:
                self.assertIsNone(bundle.testzip())
                for name in (*expected, 'effect-screenshot.jsonl', 'effect-screenshot.jsonl.1'):
                    self.assertIn(name, bundle.namelist())
                self.assertNotIn('.runtime.json', bundle.namelist())
                self.assertNotIn(b'do-not-export', b''.join(bundle.read(name) for name in bundle.namelist()))
                manifest = json.loads(bundle.read('manifest.json'))
                self.assertIn('windows-capture', manifest['dependency_status'])
                self.assertEqual(len(manifest['source_hashes']['requirements.lock']), 64)
                self.assertIn(b'WGC unavailable', bundle.read('detector.jsonl'))


if __name__ == '__main__':
    unittest.main()

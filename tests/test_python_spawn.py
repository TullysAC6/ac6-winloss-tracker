import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import python_spawn as spawn


class SpawnContract(unittest.TestCase):
    def test_scrubs_environment_without_changing_parent(self):
        original = {'PYTHONPATH': 'hostile', 'PythonHome': 'hostile', 'PYTHONUSERBASE': 'bad',
                    'PYTHONSTARTUP': 'bad', 'PYTHONOPTIMIZE': '2', 'PYTHONINSPECT': '1', '__PYVENV_LAUNCHER__': 'bad',
                    'AC6_LAUNCH_NONCE': 'old', 'LOCALAPPDATA': 'fixture', 'PATH': 'keep'}
        result = spawn.clean_environment(original)
        self.assertEqual(result, {'LOCALAPPDATA': 'fixture', 'PATH': 'keep',
                                 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
        self.assertEqual(original['PYTHONPATH'], 'hostile')

    def test_non_venv_uses_normal_executable(self):
        with patch.object(sys, 'prefix', sys.base_prefix):
            command, env = spawn.python_command(['entry.py'])
        self.assertEqual(command, [sys.executable, 'entry.py'])
        self.assertNotIn('__PYVENV_LAUNCHER__', env)

    def test_venv_uses_actual_python_and_preserves_windowless_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, venv = root / 'base', root / 'venv'
            base.mkdir()
            (venv / 'Scripts').mkdir(parents=True)
            for name in ('python.exe', 'pythonw.exe'):
                (base / name).touch()
                (venv / 'Scripts' / name).touch()
            cfg = venv / 'pyvenv.cfg'
            cfg.write_text(f'home = {base}\ninclude-system-site-packages = false\n'
                           f'version = {sys.version_info.major}.{sys.version_info.minor}.0\n')
            with patch.multiple(sys, platform='win32', prefix=str(venv), base_prefix=str(base),
                                executable=str(venv / 'Scripts' / 'python.exe'),
                                _base_executable=str(base / 'python.exe')):
                for windowless, name in ((False, 'python.exe'), (True, 'pythonw.exe')):
                    command, env = spawn.python_command(['entry.py'], windowless=windowless)
                    self.assertEqual(command[0], str(base / name))
                    self.assertEqual(env['__PYVENV_LAUNCHER__'], str(venv / 'Scripts' / name))
                (base / 'pythonw.exe').unlink()
                with self.assertRaisesRegex(RuntimeError, 'ENV-VENV-BROKEN'):
                    spawn.python_command([], windowless=True)
                cfg.write_text(f'home = {base}\ninclude-system-site-packages = true\nversion = 3.13.0\n')
                with self.assertRaises(RuntimeError):
                    spawn.python_command([])

    def test_missing_base_fails_closed(self):
        with patch.multiple(sys, platform='win32', prefix='missing-venv', base_prefix='base',
                            _base_executable=None):
            with self.assertRaisesRegex(RuntimeError, 'Re-run the installer'):
                spawn.python_command([])

    def test_nonce_pid_and_handle_are_all_required(self):
        process = SimpleNamespace(pid=123, ac6_launch_nonce='a' * 64, poll=lambda: None)
        valid = {'pid': 123, 'launch_nonce': 'a' * 64}
        self.assertTrue(spawn.matches_launch(valid, process))
        for nonce in (None, '', 'a' * 63, 'a' * 65, 'A' * 64, 'b' * 64, ['a'] * 64):
            self.assertFalse(spawn.matches_launch(dict(valid, launch_nonce=nonce), process))
        for pid in (True, '123', 124, 0):
            self.assertFalse(spawn.matches_launch(dict(valid, pid=pid), process))
        process.ac6_launch_nonce = 'c' * 64
        self.assertFalse(spawn.matches_launch(valid, process), 'replayed launch must fail')
        process.ac6_launch_nonce = 'a' * 64
        process.poll = lambda: 0
        self.assertFalse(spawn.matches_launch(valid, process), 'exited owned handle must fail')


if __name__ == '__main__':
    unittest.main()

"""Own the actual Windows Python process, including inside a venv.

Windows venv executables redirect to another process. Use CPython's own
multiprocessing convention so Popen's handle belongs to the interpreter doing
the work. Never recover ownership by opening a PID supplied in a runtime file.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

NONCE_ENV = 'AC6_LAUNCH_NONCE'
_NONCE = re.compile(r'[0-9a-f]{64}\Z')
_CONTAMINATION = ('PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE', 'PYTHONSTARTUP',
                  'PYTHONOPTIMIZE', 'PYTHONINSPECT', 'PYTHONEXECUTABLE', 'PYTHONPLATLIBDIR',
                  '__PYVENV_LAUNCHER__', NONCE_ENV)


def clean_environment(environment=None):
    result = dict(os.environ if environment is None else environment)
    for name in list(result):
        if name.upper() in _CONTAMINATION:
            result.pop(name)
    result['PYTHONUTF8'] = '1'
    result['PYTHONIOENCODING'] = 'utf-8'
    return result


def python_command(arguments, *, environment=None, windowless=None):
    env = clean_environment(environment)
    executable = Path(sys.executable)
    if windowless is not None and sys.platform == 'win32':
        executable = executable.with_name('pythonw.exe' if windowless else 'python.exe')
    if sys.platform == 'win32' and sys.prefix != sys.base_prefix:
        try:
            cfg = Path(sys.prefix, 'pyvenv.cfg').read_text(encoding='utf-8')
            fields = dict(line.split('=', 1) for line in cfg.splitlines() if '=' in line)
            fields = {k.strip().lower(): v.strip() for k, v in fields.items()}
            base = Path(sys._base_executable).with_name(executable.name)
            expected_version = '.'.join(map(str, sys.version_info[:2]))
            if (not base.is_absolute() or not base.is_file()
                    or base.name.lower() not in ('python.exe', 'pythonw.exe')
                    or base.parent.resolve() != Path(fields['home']).resolve()
                    or fields.get('include-system-site-packages', '').lower() != 'false'
                    or '.'.join(fields['version'].split('.')[:2]) != expected_version
                    or executable.parent.parent.resolve() != Path(sys.prefix).resolve()
                    or not executable.is_file()):
                raise ValueError('venv/base mismatch')
        except (OSError, AttributeError, KeyError, TypeError, ValueError) as error:
            raise RuntimeError('[ENV-VENV-BROKEN] Python environment is unavailable or incompatible. '
                               'Re-run the installer to repair it.') from error
        env['__PYVENV_LAUNCHER__'] = str(executable)
        executable = base
    return [str(executable), *map(str, arguments)], env


def spawn_python(arguments, *, environment=None, windowless=None, **kwargs):
    command, env = python_command(arguments, environment=environment, windowless=windowless)
    nonce = secrets.token_hex(32)
    env[NONCE_ENV] = nonce
    process = subprocess.Popen(command, env=env, **kwargs)
    process.ac6_launch_nonce = nonce
    return process


def launch_nonce():
    value = os.environ.get(NONCE_ENV, '')
    return value if isinstance(value, str) and _NONCE.fullmatch(value) else ''


def matches_launch(runtime, process):
    if not isinstance(runtime, dict) or process.poll() is not None:
        return False
    expected = getattr(process, 'ac6_launch_nonce', None)
    observed = runtime.get('launch_nonce')
    return (type(runtime.get('pid')) is int and runtime['pid'] == process.pid
            and isinstance(expected, str) and _NONCE.fullmatch(expected) is not None
            and isinstance(observed, str) and _NONCE.fullmatch(observed) is not None
            and secrets.compare_digest(observed, expected))


def stop_owned_process(process, timeout=3):
    """Only the parent's retained handle is a termination authority."""
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)

"""Actual venv/pythonw PID ownership and pre-native multiprocessing containment."""
import ctypes
from ctypes import wintypes
import json
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from owned_worker import KillOnCloseJob, owned_entry, stop_process
from python_spawn import spawn_python, stop_owned_process, matches_launch


def worker(output):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    contained = wintypes.BOOL()
    # A NULL job handle only proves membership of *some* job, which an ambient
    # job already satisfies. Containment of the owned job is proven by the
    # parent, which is the only side holding that job handle.
    assert kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(contained))
    Path(output).write_text(json.dumps({'pid': os.getpid(), 'in_any_job': bool(contained.value)}))


def probe(output):
    import site
    import ac6_owned_probe
    result = {'pid': os.getpid(), 'launch_nonce': os.environ['AC6_LAUNCH_NONCE'],
              'prefix': sys.prefix, 'base_prefix': sys.base_prefix,
              'dependency': ac6_owned_probe.__file__, 'user_site': site.ENABLE_USER_SITE,
              'path': sys.path}
    ctx = mp.get_context('spawn')
    ready = ctx.Event()
    child = ctx.Process(target=owned_entry, args=(ready, worker, (output + '.worker',)))
    child.start()
    job = None
    try:
        job = KillOnCloseJob(child.pid)
        assert job.handle, 'containment was not installed'
        # The parent owns a handle to the actual child, so this cannot be
        # satisfied by a recycled PID, and naming the job handle cannot be
        # satisfied by an inherited ambient job.
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        owned = wintypes.BOOL()
        queried = kernel.IsProcessInJob(child.sentinel, job.handle, ctypes.byref(owned))
        assert queried, ctypes.WinError(ctypes.get_last_error())
        assert owned.value, 'child is not contained by the owned job before native work'
        assert not Path(output + '.worker').exists(), 'worker escaped the readiness barrier'
        result['worker_in_owned_job'] = bool(owned.value)
        ready.set()
        child.join(10)
        assert child.exitcode == 0
        evidence = json.loads(Path(output + '.worker').read_text())
        assert evidence['pid'] == child.pid
        result['worker'] = evidence
    finally:
        if job:
            job.close()
        stop_process(child)
    Path(output).write_text(json.dumps(result))
    time.sleep(30)


@unittest.skipUnless(sys.platform == 'win32', 'Windows process topology')
class VenvTopology(unittest.TestCase):
    def test_console_and_windowless_actual_pid_and_worker(self):
        with tempfile.TemporaryDirectory(prefix='ac6-spawn-') as temporary:
            root = Path(temporary)
            venv = root / 'venv'
            subprocess.run([sys._base_executable, '-m', 'venv', '--without-pip', str(venv)],
                           check=True, timeout=40)
            (venv / 'Lib/site-packages/ac6_owned_probe.py').write_text('OWNED = True\n')
            # Run the same probe through both venv entry points. The tiny parent
            # imports the production helper from inside that venv.
            parent = root / 'parent.py'
            parent.write_text('''import json,sys,time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from python_spawn import spawn_python, stop_owned_process, matches_launch
p = spawn_python([sys.argv[2], '--probe', sys.argv[3]], windowless=sys.argv[4]=='1')
try:
 end=time.monotonic()+15
 while not Path(sys.argv[3]).exists() and p.poll() is None and time.monotonic()<end: time.sleep(.05)
 r=json.loads(Path(sys.argv[3]).read_text())
 assert matches_launch(r,p), (p.pid,r)
 assert Path(r['prefix'])==Path(sys.prefix) and r['prefix']!=r['base_prefix']
 assert Path(r['dependency']).is_relative_to(Path(sys.prefix))
 assert r['user_site'] is False
 assert all('site-packages' not in v.lower() or Path(v).is_relative_to(Path(sys.prefix)) for v in r['path'])
finally: stop_owned_process(p)
assert p.poll() is not None
''', encoding='utf-8')
            for windowless in (False, True):
                with self.subTest(windowless=windowless):
                    output = root / ('windowless.json' if windowless else 'console.json')
                    env = os.environ.copy()
                    for key in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONUSERBASE', 'PYTHONSTARTUP'):
                        env.pop(key, None)
                    env['__PYVENV_LAUNCHER__'] = str(venv / 'Scripts/python.exe')
                    subprocess.run([sys._base_executable, str(parent), str(ROOT), __file__,
                                    str(output), str(int(windowless))], env=env, check=True, timeout=30)
                    self.assertTrue(json.loads(output.read_text())['worker_in_owned_job'])


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--probe':
        probe(sys.argv[2])
    else:
        unittest.main()

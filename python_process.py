"""Launch the real Windows interpreter while retaining the venv configuration."""
import os
import subprocess
import sys


def spawn_python(arguments, *, env=None, **kwargs):
    environment = dict(os.environ if env is None else env)
    executable = sys.executable
    if os.name == "nt" and sys.prefix != sys.base_prefix:
        # CPython's venv redirector uses this same environment variable. Avoid
        # its intermediate process so Popen retains the actual process handle.
        environment["__PYVENV_LAUNCHER__"] = sys.executable
        executable = sys._base_executable
    return subprocess.Popen([executable, *arguments], env=environment, **kwargs)

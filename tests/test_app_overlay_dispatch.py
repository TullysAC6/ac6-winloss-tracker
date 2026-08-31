import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app

calls = []
fake = types.SimpleNamespace(main=lambda argv: calls.append(argv) or 0)
original = sys.modules.get("game_overlay")
try:
    sys.modules["game_overlay"] = fake
    for frozen in (False, True):
        if frozen:
            sys.frozen = True
        elif hasattr(sys, "frozen"):
            del sys.frozen
        for argv, expected in [
            (["app.py", "--overlay"], []),
            (["app.py", "--overlay", "--debug"], ["--debug"]),
        ]:
            sys.argv = argv
            try:
                app.main()
            except SystemExit as error:
                assert error.code == 0
            assert calls.pop() == expected
finally:
    if hasattr(sys, "frozen"):
        del sys.frozen
    if original is None:
        sys.modules.pop("game_overlay", None)
    else:
        sys.modules["game_overlay"] = original

print("source/frozen overlay dispatch strips internal flag: OK")


class StubbornOverlay:
    pid = 123
    returncode = None
    events = []

    def poll(self):
        return None

    def wait(self, timeout):
        self.events.append(("wait", timeout))
        if len([event for event in self.events if event[0] == "wait"]) < 3:
            raise app.subprocess.TimeoutExpired("overlay", timeout)
        self.returncode = -9

    def terminate(self):
        self.events.append(("terminate", None))

    def kill(self):
        self.events.append(("kill", None))


stubborn = StubbornOverlay()
app._stop_overlay(stubborn, timeout=0.01)
assert [event[0] for event in stubborn.events] == [
    "wait", "terminate", "wait", "kill", "wait"
]
print("overlay graceful wait / terminate / kill orphan prevention: OK")

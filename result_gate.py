import threading
import time


class ResultGate:
    """One cooldown gate shared by automatic and manual result sources."""

    def __init__(self):
        self.lock = threading.RLock()
        self.last_accepted_at = None

    def try_accept(self, cooldown_seconds, now=None):
        now = time.monotonic() if now is None else float(now)
        with self.lock:
            if self.last_accepted_at is not None:
                if now - self.last_accepted_at < cooldown_seconds:
                    return False
            self.last_accepted_at = now
            return True

    def clear_for_manual_correction(self):
        with self.lock:
            self.last_accepted_at = None

    def lock_now(self, now=None):
        now = time.monotonic() if now is None else float(now)
        with self.lock:
            self.last_accepted_at = now

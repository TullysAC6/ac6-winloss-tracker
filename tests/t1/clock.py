"""Deterministic virtual time for one replay case.

Four times are kept distinct:

* sequence time - ``at_ms`` of each step in the fixture;
* capture time  - when the frame was captured (``captured_at``), which the
  state machine observes;
* processing time - capture time plus a fixed ``processing_ms``, which is what
  ``time.monotonic()`` returns inside the replayed modules and therefore what
  ``external_mutation()`` and the ResultGate use as ``now``;
* cooldown time - derived from the two above by production code itself.

Real time is never used for fixture timing, and real time is always used for
worker, case and suite timeouts. The global ``time`` module is never patched:
only the ``time`` name inside the replayed production modules is rebound.
"""
from __future__ import annotations


class VirtualClock:
    # Starts well past the 5 s cooldown so an unarmed detector's initial
    # last_counted_at of 0.0 never blocks arming, exactly as on a real host.
    BASE_SECONDS = 100.0
    EPOCH_OFFSET = 1_700_000_000.0

    def __init__(self, processing_ms):
        if type(processing_ms) is not int or not 0 <= processing_ms <= 1000:
            raise ValueError("processing_ms must be an integer between 0 and 1000")
        self.processing_seconds = processing_ms / 1000.0
        self.capture_seconds = self.BASE_SECONDS

    def seconds(self, at_ms):
        return self.BASE_SECONDS + at_ms / 1000.0

    def set_step(self, at_ms):
        self.capture_seconds = self.seconds(at_ms)

    def processing_now(self):
        return self.capture_seconds + self.processing_seconds


class ModuleTime:
    """Replacement for the ``time`` name inside one replayed production module.

    Only the functions those modules call are provided. Anything else raises
    AttributeError, so a future production use of another clock fails the
    replay instead of silently reading real time.
    """

    def __init__(self, clock):
        self._clock = clock

    def monotonic(self):
        return self._clock.processing_now()

    def time(self):
        return VirtualClock.EPOCH_OFFSET + self._clock.processing_now()

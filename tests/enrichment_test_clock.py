"""Test-only clocks for enrichment; lifecycle/watchdog clocks stay real."""
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch


@contextmanager
def fixture_clock():
    """Keep real _Deadline/SQLite logic, but never expire on a runner stall.

    Replace the storage module's clock reference, never the shared time module.
    This also covers the facade's imported _Deadline class. Enter after any
    module reload; do not use for tests whose subject is elapsed time.
    """
    import enrichment_store

    assert enrichment_store._BUDGET == 0.100
    with patch.object(enrichment_store, "time", SimpleNamespace(monotonic=lambda: 0.0)):
        yield


@contextmanager
def real_clock():
    """Explicitly opt timing negative controls back into production behavior."""
    import enrichment_store

    assert enrichment_store._BUDGET == 0.100
    with patch.object(enrichment_store, "time", time):
        yield

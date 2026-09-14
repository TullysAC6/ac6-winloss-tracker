"""T1 fixture replay harness (issue #14).

Stored, reviewed pixels are replayed through the production recognition path:
``ResultClassifier``, the real ``ResultDetector.run`` loop with its motion
helpers and ``ResultStateMachine``, and the real ``ResultGate``. Only native
capture is replaced. See ``tests/fixtures/README.md`` for the corpus contract and
``tests/run_t1.py`` for the canonical entry point.

The parent side of this package (schema, corpus, runner, reports) never imports
a production module. Only ``worker.py`` does, and only after it has proved its
environment is isolated.
"""

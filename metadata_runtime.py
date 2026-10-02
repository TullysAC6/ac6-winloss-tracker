"""Optional, assisted header acquisition. Core notifications never wait for this owner.

Only structured memory evidence crosses capture -> match. A first accepted DRAW
also consumes it. No time/window heuristic assigns evidence to an actual match:
the authenticated user attests the exact candidate event before any sidecar write.
"""
from __future__ import annotations

import secrets
import struct
import threading
import time

PENDING_TTL = 600.0
CONFIRM_TTL = 60.0
VERSION = "rank-single-header.v1"


def feature_enabled():
    import preferences
    try:
        return preferences.load().get("match_metadata_detection") is True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def default_target():
    from lobby_capture import current_target
    return current_target()


def default_worker(target, cancel):
    from lobby_capture import acquire
    return acquire(target, cancel)


def default_service(root):
    from optional_enrichment import OptionalEnrichmentService
    return OptionalEnrichmentService(root, active=True)


class MetadataRuntime:
    def __init__(self, root, *, enabled=feature_enabled, target=default_target,
                 worker=default_worker, service=default_service, clock=time.monotonic):
        self.root, self.enabled, self.target = root, enabled, target
        self.worker, self.service_factory, self.clock = worker, service, clock
        self.lock = threading.Lock()
        self.lost = threading.Event()
        self.cancelled = threading.Event()
        self.epoch = 0
        self.closed = False
        self.task = None
        self.pending = None
        self.candidate = None
        self.service = None
        self.state = "idle"
        self.reason = ""
        self.request_id = None
        self.last = None

    def _discard(self, reason):
        self.epoch += 1
        self.cancelled.set()
        self.pending = self.candidate = None
        self.state, self.reason = "unknown", reason

    def notify(self, kind, *, result=None, event_id=None, decided_at=None, witness=None):
        """Constant-memory, nonblocking seam; no preference, native or DB I/O.

        Busy means uncertain evidence, not delayed core. The loss latch is checked
        at every optional admission/completion. It cannot turn a lost result into
        a candidate for a later result.
        """
        if not self.lock.acquire(blocking=False):
            self.lost.set()
            return
        try:
            if self.lost.is_set():
                self._discard("notification_lost")
                self.lost.clear()
            if kind != "result":
                if kind == "decision" and self.state != "acquiring":
                    return
                self._discard(kind)
                return
            now = self.clock()
            pending, self.pending = self.pending, None
            # Every accepted result invalidates an in-progress acquisition and
            # any previous confirmation, including nonpersisted accepted DRAW.
            if (self.closed or self.task is not None or self.candidate is not None
                    or pending is None or now >= pending["expires"]
                    or (decided_at is not None and pending["completed_at"] > decided_at)):
                self._discard("result_consumed")
                return
            if (result not in ("win", "loss") or not isinstance(event_id, str) or not event_id
                    or not isinstance(witness, tuple) or len(witness) != 3
                    or witness[0] != event_id or witness[2] != result
                    or type(witness[1]) not in (int, float)):
                self._discard("result_without_match")
                return
            self.epoch += 1
            self.candidate = {**pending, "event_id": event_id, "result": result,
                              "accepted_at": now,
                              "recorded_at": witness[1],
                              "witness": (event_id, 1, struct.pack(">d", witness[1]), result),
                              "expires": min(pending["expires"], now + CONFIRM_TTL)}
            self.state, self.reason = "confirmation", "explicit_same_match_required"
        finally:
            self.lock.release()

    def _sync_state(self):
        if not self.enabled():
            self.cancel("disabled")
            return False, None
        with self.lock:
            if self.lost.is_set():
                self._discard("notification_lost")
                self.lost.clear()
            item = self.candidate or self.pending
            if item and self.clock() >= item["expires"]:
                self._discard("expired")
            return not self.closed, self.epoch

    def _sync(self):
        return self._sync_state()[0]

    def cancel(self, reason="cancelled"):
        with self.lock:
            self._discard(reason)
            service = self.service
        # Control/task path only. Never called by the core notification seam.
        if service is not None:
            try:
                service.deactivate()
            except Exception:
                pass
        return {"state": "unknown", "reason": reason}

    def _valid(self, epoch):
        return (not self.closed and self.epoch == epoch and not self.lost.is_set()
                and not self.cancelled.is_set())

    def _start(self, work, name):
        self.task = threading.Thread(target=work, name=name, daemon=True)
        try:
            self.task.start()
        except Exception:
            self.task = None
            self._discard("task_start_failed")
            raise

    def acquire(self):
        active, admission_epoch = self._sync_state()
        if not active:
            return {"state": "disabled"}
        # Target discovery outside the state lock and core critical section.
        target = self.target()
        if not self.enabled():
            self.cancel("disabled")
            return {"state": "disabled"}
        if target is None:
            self.cancel("target_unavailable")
            return {"state": "unknown", "reason": "target_unavailable"}
        with self.lock:
            if self.task is not None or self.closed:
                return {"state": "busy"}
            if self.epoch != admission_epoch or self.lost.is_set():
                return {"state": "rejected", "reason": "acquisition_revoked"}
            self._discard("replaced")
            self.cancelled = threading.Event()
            epoch, cancel = self.epoch, self.cancelled
            request_id = secrets.token_hex(16)
            self.request_id = request_id
            self.state, self.reason = "acquiring", ""

            def work():
                result = None
                try:
                    with self.lock:
                        if not self._valid(epoch):
                            return
                    if not self.enabled():
                        self.cancel("disabled")
                        return
                    result = self.worker(target, cancel)
                    valid = self.enabled() and self.target() == target
                    with self.lock:
                        if self._valid(epoch) and valid:
                            if (isinstance(result, dict) and result.get("status") == "recognized"
                                    and result.get("version") == VERSION
                                    and result.get("source") == "direct_header"
                                    and result.get("match_type") == "ranked"
                                    and result.get("match_format") == "single"
                                    and result.get("cleaned") is True
                                    and type(result.get("captured_at")) in (int, float)
                                    and 0 <= self.clock() - result["captured_at"] <= 5.0):
                                self.pending = {"request_id": request_id, "target": target,
                                                "captured_at": result["captured_at"],
                                                "completed_at": self.clock(),
                                                "expires": result["captured_at"] + PENDING_TTL,
                                                "version": VERSION, "source": "direct_header"}
                                self.state, self.reason = "pending", "awaiting_result"
                            else:
                                self._discard("unknown")
                        elif self._valid(epoch):
                            self._discard("target_or_preference_changed")
                except Exception:
                    with self.lock:
                        if self._valid(epoch):
                            self._discard("acquisition_failed")
                finally:
                    with self.lock:
                        # Cleanup failure permanently prevents replacement.
                        if isinstance(result, dict) and result.get("cleaned") is False:
                            self.closed = True
                            self._discard("worker_not_reaped")
                        self.task = None
            self._start(work, "metadata-acquisition")
            return {"state": "acquiring", "request_id": request_id}

    def status(self):
        active = self._sync()
        target = self.target() if active and (self.pending or self.candidate) else None
        with self.lock:
            item = self.candidate or self.pending
            if item and target != item["target"]:
                self._discard("target_changed")
                item = None
            response = {"state": self.state if active else "disabled", "reason": self.reason,
                        "busy": self.task is not None, "request_id": self.request_id}
            if item:
                response.update({k: item[k] for k in ("request_id", "captured_at", "expires")})
                response.update(match_type="ranked", match_format="single", version=VERSION)
                if "event_id" in item:
                    response.update(event_id=item["event_id"], result=item["result"],
                                    accepted_at=item["accepted_at"], recorded_at=item["recorded_at"])
            if self.last:
                response["last_write"] = dict(self.last)
            return response

    def confirm(self, request_id, event_id, same_match):
        if same_match is not True or not self._sync():
            return {"state": "rejected", "reason": "explicit_confirmation_required"}
        target = self.target()
        with self.lock:
            item = self.candidate
            if (self.task is not None or item is None or self.closed
                    or item["request_id"] != request_id or item["event_id"] != event_id
                    or self.clock() >= item["expires"] or item["target"] != target):
                return {"state": "rejected", "reason": "stale_or_wrong_candidate"}
            self.candidate = None  # One-shot admission even if sidecar later fails.
            self.cancelled = threading.Event()
            epoch = self.epoch
            self.state, self.reason = "writing", ""

            def work():
                outcome = "unavailable"
                reason = "optional_failure"
                service = None
                try:
                    service = self.service_factory(self.root)
                    with self.lock:
                        if not self._valid(epoch):
                            return
                        self.service = service
                    prepared = service.prepare_binding(event_id)
                    if prepared.status != "prepared" or prepared.ticket.binding.values() != item["witness"]:
                        reason = "invalid_authority"
                        return
                    current = service.lookup_snapshot(event_id)
                    # Missing sidecar is safe to explicitly create; incompatible /
                    # unreadable authority is not evidence of an empty snapshot.
                    if current.health.reason not in ("", "missing"):
                        reason = current.health.reason
                        return
                    if current.snapshots:
                        snap = current.snapshots[0]
                        if (snap.revision != 0 or snap.match_type != "unknown" or snap.match_format != "unknown"
                                or snap.self_rank is not None or snap.opponent_rank is not None
                                or snap.recognition_status is not None or snap.recognition_version is not None):
                            outcome, reason = "conflict", "existing_snapshot"
                            return
                    elif current.status not in ("unknown", "unavailable"):
                        return
                    if not self.enabled() or self.target() != item["target"]:
                        return
                    with self.lock:
                        if not self._valid(epoch) or self.clock() >= item["expires"]:
                            return
                    from optional_enrichment import MatchSnapshot
                    snapshot = MatchSnapshot(event_id, "ranked", "single", None, None,
                                             "recognized", VERSION, revision=0)
                    saved = service.save_snapshot(prepared.ticket, snapshot,
                                                  expected_revision=0, create_missing=True)
                    outcome, reason = saved.status, saved.health.reason
                except Exception:
                    pass
                finally:
                    if service is not None:
                        try:
                            service.deactivate()
                        except Exception:
                            pass
                    with self.lock:
                        if self._valid(epoch):
                            self.last = {"event_id": event_id, "state": outcome, "reason": reason}
                            self.state, self.reason = outcome, reason
                        self.service = None
                        self.task = None
            self._start(work, "metadata-persistence")
            return {"state": "writing", "event_id": event_id}

    def shutdown(self):
        self.cancel("shutdown")
        with self.lock:
            self.closed = True
            task = self.task
        if task:
            task.join(timeout=2.0)
        return not task or not task.is_alive()

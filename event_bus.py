import json
import queue
import threading
import uuid
from collections import deque


class ClientConnection:
    def __init__(self, maxsize=200):
        self.queue = queue.Queue(maxsize=maxsize)
        self.overflow = threading.Event()


class EventBus:
    def __init__(self, history_size=300, client_queue_size=200, boot_id=None):
        self.boot_id = boot_id or uuid.uuid4().hex
        self.seq = 0
        self.history = deque(maxlen=history_size)
        self.clients = set()
        self.lock = threading.RLock()
        self.drop_count = 0
        self.client_queue_size = client_queue_size

    def _new_record_unlocked(self, event_type, payload, remember):
        self.seq += 1
        record = {
            "id": f"{self.boot_id}:{self.seq}",
            "seq": self.seq,
            "event": event_type,
            "data": json.dumps(payload, ensure_ascii=False),
        }
        if remember:
            self.history.append(record)
        return record

    def publish(self, event_type, payload, remember=True):
        with self.lock:
            record = self._new_record_unlocked(event_type, payload, remember)
            for client in list(self.clients):
                try:
                    client.queue.put_nowait(record)
                except queue.Full:
                    client.overflow.set()
                    self.drop_count += 1
            return record

    def _replay_for_unlocked(self, last_event_id):
        if not last_event_id:
            return []
        try:
            boot, seq_text = last_event_id.rsplit(":", 1)
            seq = int(seq_text)
        except (ValueError, AttributeError):
            return list(self.history)

        if boot != self.boot_id:
            return list(self.history)
        return [r for r in self.history if r["seq"] > seq]

    def register_with_snapshots(self, last_event_id, snapshot_factory):
        # One lock defines the replay boundary. No publisher can insert an
        # event between replay capture, snapshot capture and registration.
        with self.lock:
            replay = self._replay_for_unlocked(last_event_id)
            snapshots = snapshot_factory()
            client = ClientConnection(self.client_queue_size)
            self.clients.add(client)
            return client, replay, snapshots

    def unregister(self, client):
        with self.lock:
            self.clients.discard(client)

"""Durable, idempotent compensation for known failed result/undo writes.

This records only rows already known not to be counted. It is not a journal
for a process killed between the history commit and the stats write.
"""
import json
import os
from pathlib import Path


class PendingHistory:
    def __init__(self, root):
        self.path = Path(root) / "pending-history.json"

    def load(self):
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except FileNotFoundError:
            return []
        if (not isinstance(payload, dict) or payload.get("version") != 1
                or not isinstance(payload.get("event_ids"), list)):
            raise ValueError("invalid pending history recovery file")
        ids = payload["event_ids"]
        if (len(ids) > 128 or any(not isinstance(value, str) or not value
                                  or len(value) > 128 for value in ids)):
            raise ValueError("invalid pending history event identities")
        return list(dict.fromkeys(ids))

    def save(self, event_ids):
        # Keep the last committed file even when replacement fails. Empty
        # queues are written atomically too; stale IDs are harmless on replay.
        temporary = self.path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump({"version": 1, "event_ids": list(event_ids)}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

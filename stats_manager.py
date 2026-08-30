import json
import math
import os
import shutil
import threading
import time
from pathlib import Path


class StatsError(RuntimeError):
    pass


class StatsCorruptError(StatsError):
    pass


class StatsManager:
    VERSION = 3
    RECENT_MAX = 2000

    def __init__(self, root):
        root = Path(root)
        self.path = root / "stats.json"
        self.tmp = root / "stats.json.tmp"
        self.bak = root / "stats.json.bak"
        self.bak_tmp = root / "stats.json.bak.tmp"
        self.lock = threading.RLock()

    def _default(self):
        return {
            "version": self.VERSION,
            "wins": 0,
            "losses": 0,
            "streak": 0,
            "best_streak": 0,
            "recent_results": [],
        }

    @staticmethod
    def _strict_nonneg_int(name, value):
        if type(value) is not int or value < 0:
            raise StatsCorruptError(f"invalid {name}")
        return value

    @staticmethod
    def _strict_nonneg_number(name, value):
        if type(value) not in (int, float):
            raise StatsCorruptError(f"invalid {name}")
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise StatsCorruptError(f"invalid {name}")
        return value

    def _validate_current(self, raw):
        if not isinstance(raw, dict):
            raise StatsCorruptError("stats root is not an object")

        required = {
            "version",
            "wins",
            "losses",
            "streak",
            "best_streak",
            "recent_results",
        }
        if set(raw) != required:
            missing = required - set(raw)
            unknown = set(raw) - required
            details = []
            if missing:
                details.append("missing=" + ",".join(sorted(missing)))
            if unknown:
                details.append("unknown=" + ",".join(sorted(unknown)))
            raise StatsCorruptError("invalid stats schema: " + " ".join(details))

        if type(raw["version"]) is not int or raw["version"] != self.VERSION:
            raise StatsCorruptError("invalid stats version")

        wins = self._strict_nonneg_int("wins", raw["wins"])
        losses = self._strict_nonneg_int("losses", raw["losses"])
        streak = self._strict_nonneg_int("streak", raw["streak"])
        best = self._strict_nonneg_int("best_streak", raw["best_streak"])
        if best < streak:
            raise StatsCorruptError("best_streak is smaller than streak")
        if streak > wins:
            raise StatsCorruptError("streak exceeds total wins")

        rr = raw["recent_results"]
        if not isinstance(rr, list):
            raise StatsCorruptError("recent_results is not a list")
        if len(rr) > self.RECENT_MAX:
            raise StatsCorruptError("recent_results exceeds maximum history")

        clean = []
        event_required = {
            "result",
            "source",
            "ts",
            "prev_streak",
            "prev_best",
        }
        for idx, e in enumerate(rr):
            if not isinstance(e, dict) or set(e) != event_required:
                raise StatsCorruptError(f"invalid recent_results[{idx}] schema")
            if e["result"] not in ("win", "loss"):
                raise StatsCorruptError(f"invalid recent_results[{idx}].result")
            if not isinstance(e["source"], str) or not e["source"]:
                raise StatsCorruptError(f"invalid recent_results[{idx}].source")
            ts = self._strict_nonneg_number(
                f"recent_results[{idx}].ts", e["ts"]
            )
            prev_streak = self._strict_nonneg_int(
                f"recent_results[{idx}].prev_streak", e["prev_streak"]
            )
            prev_best = self._strict_nonneg_int(
                f"recent_results[{idx}].prev_best", e["prev_best"]
            )
            if prev_best < prev_streak:
                raise StatsCorruptError(
                    f"recent_results[{idx}] prev_best < prev_streak"
                )
            clean.append({
                "result": e["result"],
                "source": e["source"],
                "ts": ts,
                "prev_streak": prev_streak,
                "prev_best": prev_best,
            })

        return {
            "version": self.VERSION,
            "wins": wins,
            "losses": losses,
            "streak": streak,
            "best_streak": best,
            "recent_results": clean,
        }

    def _migrate_v2(self, raw):
        """Migrate v11/v12 version-2 stats to version 3 safely.

        v11 could store synthetic source='migration', ts=0 undo entries whose
        prev_streak metadata was not trustworthy. Keep only the suffix AFTER
        the last such entry; cumulative totals/streak/best stay authoritative.
        """
        if not isinstance(raw, dict):
            raise StatsCorruptError("v2 stats root is not an object")

        required = {
            "version", "wins", "losses", "streak", "best_streak",
            "recent_results",
        }
        if set(raw) != required or raw.get("version") != 2:
            raise StatsCorruptError("invalid v2 stats schema")

        def strict_i(name):
            value = raw[name]
            if type(value) is not int or value < 0:
                raise StatsCorruptError(f"invalid v2 {name}")
            return value

        wins = strict_i("wins")
        losses = strict_i("losses")
        streak = strict_i("streak")
        best = strict_i("best_streak")
        if streak > wins or best < streak:
            raise StatsCorruptError("invalid v2 streak values")

        rr = raw["recent_results"]
        if not isinstance(rr, list) or len(rr) > self.RECENT_MAX:
            raise StatsCorruptError("invalid v2 recent_results")

        clean = []
        last_unsafe = -1
        event_required = {"result", "source", "ts", "prev_streak", "prev_best"}
        for idx, e in enumerate(rr):
            if not isinstance(e, dict) or set(e) != event_required:
                raise StatsCorruptError(f"invalid v2 recent_results[{idx}] schema")
            if e["result"] not in ("win", "loss"):
                raise StatsCorruptError(f"invalid v2 recent_results[{idx}].result")
            if not isinstance(e["source"], str) or not e["source"]:
                raise StatsCorruptError(f"invalid v2 recent_results[{idx}].source")
            ts = self._strict_nonneg_number(f"v2 recent_results[{idx}].ts", e["ts"])
            ps = self._strict_nonneg_int(f"v2 recent_results[{idx}].prev_streak", e["prev_streak"])
            pb = self._strict_nonneg_int(f"v2 recent_results[{idx}].prev_best", e["prev_best"])
            if pb < ps:
                raise StatsCorruptError(f"v2 recent_results[{idx}] prev_best < prev_streak")
            clean.append({
                "result": e["result"], "source": e["source"], "ts": ts,
                "prev_streak": ps, "prev_best": pb,
            })
            if e["source"] == "migration" and ts == 0.0:
                last_unsafe = idx

        if last_unsafe >= 0:
            clean = clean[last_unsafe + 1:]

        return {
            "version": self.VERSION,
            "wins": wins, "losses": losses, "streak": streak,
            "best_streak": best, "recent_results": clean,
        }

    def _looks_like_known_legacy(self, raw):
        if not isinstance(raw, dict):
            return False
        required = {"wins", "losses", "streak", "best_streak", "results"}
        if set(raw) != required:
            return False
        for name in ("wins", "losses", "streak", "best_streak"):
            value = raw.get(name)
            if type(value) is not int or value < 0:
                return False
        results = raw.get("results")
        return (
            isinstance(results, list)
            and all(x in ("win", "loss") for x in results)
        )

    def _migrate_legacy(self, raw):
        if not isinstance(raw, dict):
            raise StatsCorruptError("legacy stats root is not an object")

        # Old v9/v10/v11 files used explicit cumulative values. Preserve those
        # values, but intentionally discard old results[] as undo metadata:
        # the history may be partial/truncated, so its prev_streak cannot be
        # reconstructed safely.
        def legacy_int(name, default=0):
            value = raw.get(name, default)
            if type(value) is not int or value < 0:
                raise StatsCorruptError(f"invalid legacy {name}")
            return value

        wins = legacy_int("wins")
        losses = legacy_int("losses")
        streak = legacy_int("streak")
        best = legacy_int("best_streak", streak)

        if streak > wins:
            raise StatsCorruptError("legacy streak exceeds total wins")
        best = max(best, streak)

        return {
            "version": self.VERSION,
            "wins": wins,
            "losses": losses,
            "streak": streak,
            "best_streak": best,
            "recent_results": [],
        }

    def _parse(self, raw):
        if not isinstance(raw, dict):
            raise StatsCorruptError("stats root is not an object")

        version = raw.get("version", None)

        if version == self.VERSION:
            return self._validate_current(raw)
        if version == 2:
            return self._migrate_v2(raw)
        if version is None and self._looks_like_known_legacy(raw):
            return self._migrate_legacy(raw)

        raise StatsCorruptError("unsupported or invalid stats version/schema")

    @staticmethod
    def _read_json(path):
        try:
            with Path(path).open("r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
            raise StatsCorruptError(f"cannot read {Path(path).name}: {e}") from e

    def _load_file_validated(self, path):
        try:
            return self._parse(self._read_json(path))
        except StatsCorruptError:
            raise
        except Exception as e:
            raise StatsCorruptError(
                f"invalid {Path(path).name}: {type(e).__name__}: {e}"
            ) from e

    def _restore_main_unlocked(self, stats):
        clean = self._validate_current(stats)
        with self.tmp.open("w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(self.tmp, self.path)
        return clean

    def _load_unlocked(self):
        if not self.path.exists():
            if self.bak.exists():
                recovered = self._load_file_validated(self.bak)
                print("[stats] stats.json missing; restoring from backup")
                return self._restore_main_unlocked(recovered)
            return self._default()

        try:
            return self._load_file_validated(self.path)
        except StatsCorruptError as main_err:
            if self.bak.exists():
                try:
                    recovered = self._load_file_validated(self.bak)
                    print(f"[stats] main damaged; using backup: {main_err}")
                    return recovered
                except StatsCorruptError as bak_err:
                    raise StatsCorruptError(
                        f"main and backup are corrupt: main={main_err}; backup={bak_err}"
                    ) from bak_err
            raise StatsCorruptError(
                f"stats.json is corrupt and no valid backup exists: {main_err}"
            ) from main_err

    def _main_is_strictly_valid(self):
        if not self.path.exists():
            return False
        try:
            raw = self._read_json(self.path)
            # For backup rotation, only CURRENT strict v3 is considered valid.
            # A legacy file can be migrated for use, but is not copied over a
            # previously-good v2 backup before migration save succeeds.
            self._validate_current(raw)
            return True
        except StatsCorruptError:
            return False

    def _atomic_backup_current_main(self):
        if not self._main_is_strictly_valid():
            return

        # Keep the backup temp file open for writing while flushing it.
        # On Windows os.fsync() maps to the CRT commit operation and can raise
        # OSError(EBADF, "Bad file descriptor") for a read-only descriptor.
        # The previous implementation copied the file, reopened it as "rb",
        # then fsynced that read-only descriptor; the first result could save,
        # while the next result failed during backup rotation.
        with self.path.open("rb") as src, self.bak_tmp.open("wb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(self.bak_tmp, self.bak)

    def _save_unlocked(self, stats):
        clean = self._validate_current(stats)
        self._atomic_backup_current_main()

        with self.tmp.open("w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(self.tmp, self.path)
        return clean

    def snapshot(self):
        with self.lock:
            s = self._load_unlocked()
            return {
                "version": s["version"],
                "wins": s["wins"],
                "losses": s["losses"],
                "streak": s["streak"],
                "best_streak": s["best_streak"],
                "recent_results": [dict(x) for x in s["recent_results"]],
            }

    def add(self, result, source):
        if result not in ("win", "loss"):
            raise ValueError(result)
        if not isinstance(source, str) or not source:
            raise ValueError("source is required")

        with self.lock:
            s = self._load_unlocked()
            prev_streak = s["streak"]
            prev_best = s["best_streak"]

            if result == "win":
                s["wins"] += 1
                s["streak"] += 1
                s["best_streak"] = max(s["best_streak"], s["streak"])
            else:
                s["losses"] += 1
                s["streak"] = 0

            s["recent_results"].append({
                "result": result,
                "source": source,
                "ts": time.time(),
                "prev_streak": prev_streak,
                "prev_best": prev_best,
            })
            s["recent_results"] = s["recent_results"][-self.RECENT_MAX:]
            return self._save_unlocked(s)

    def undo(self):
        with self.lock:
            s = self._load_unlocked()
            if not s["recent_results"]:
                return s, None

            e = s["recent_results"].pop()
            if e["result"] == "win":
                s["wins"] = max(0, s["wins"] - 1)
            else:
                s["losses"] = max(0, s["losses"] - 1)

            s["streak"] = e["prev_streak"]
            s["best_streak"] = max(s["streak"], e["prev_best"])
            return self._save_unlocked(s), e

    def reset(self):
        with self.lock:
            # A reset is still a mutation. If existing main+backup are both
            # corrupt, refuse to overwrite them silently. The user can make
            # an intentional recovery by moving/deleting the damaged files.
            if self.path.exists():
                self._load_unlocked()
            return self._save_unlocked(self._default())

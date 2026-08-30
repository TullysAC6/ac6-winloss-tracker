import json
import tempfile
import threading
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stats_manager import StatsCorruptError, StatsManager


def write(path, obj):
    Path(path).write_text(json.dumps(obj), encoding="utf-8")


def v3(wins=0, losses=0, streak=0, best=0, rr=None):
    return {
        "version": 3,
        "wins": wins,
        "losses": losses,
        "streak": streak,
        "best_streak": best,
        "recent_results": [] if rr is None else rr,
    }

# Corrupt main must recover from good backup and must not destroy it on save.
d = Path(tempfile.mkdtemp())
m = StatsManager(d)
write(m.path, v3(wins="oops", losses=3))
write(m.bak, v3(wins=42, losses=7))
s = m.snapshot()
assert (s["wins"], s["losses"]) == (42, 7)
s = m.add("win", "test")
assert (s["wins"], s["losses"]) == (43, 7)
b = json.loads(m.bak.read_text(encoding="utf-8"))
assert (b["wins"], b["losses"]) == (42, 7)
print("corrupt main -> backup recovery without backup destruction: OK")

# Broken recent_results ts must also recover from backup.
d = Path(tempfile.mkdtemp()); m = StatsManager(d)
bad = v3(wins=2, losses=1, streak=1, best=1, rr=[{
    "result":"win","source":"x","ts":"oops","prev_streak":0,"prev_best":0
}])
write(m.path, bad); write(m.bak, v3(wins=9, losses=4, streak=2, best=5))
s = m.snapshot()
assert (s["wins"], s["losses"], s["streak"]) == (9,4,2)
print("bad recent_results.ts -> backup recovery: OK")

# Legacy migration preserves cumulative totals but discards unsafe undo metadata.
d = Path(tempfile.mkdtemp()); m = StatsManager(d)
legacy = {"wins":10,"losses":0,"streak":10,"best_streak":10,"results":["win","win"]}
write(m.path, legacy)
s = m.snapshot()
assert (s["wins"], s["streak"], s["best_streak"]) == (10,10,10)
assert s["recent_results"] == []
s2, removed = m.undo()
assert removed is None
assert (s2["wins"], s2["streak"], s2["best_streak"]) == (10,10,10)
print("legacy migration cannot corrupt streak via Undo: OK")

# Cumulative totals remain independent of bounded undo history.
d = Path(tempfile.mkdtemp()); m = StatsManager(d)
write(m.path, v3(wins=15000, losses=5000, streak=7, best=31))
s = m.snapshot()
assert (s["wins"], s["losses"], s["best_streak"]) == (15000,5000,31)
print(">10,000 cumulative stats: OK")

# Read-modify-write transaction under concurrent mutations.
d = Path(tempfile.mkdtemp()); m = StatsManager(d)
write(m.path, v3())
barrier = threading.Barrier(3)
def add(result):
    barrier.wait(); m.add(result, "thread")
t1=threading.Thread(target=add,args=("win",)); t2=threading.Thread(target=add,args=("loss",))
t1.start();t2.start();barrier.wait();t1.join();t2.join()
s=m.snapshot()
assert s["wins"]==1 and s["losses"]==1
print("concurrent mutation transaction: OK")


# v11->v12 style version-2 file containing unsafe migration entries:
# keep cumulative values and only the safe suffix after the last migration event.
d = Path(tempfile.mkdtemp()); m = StatsManager(d)
v2_chain = {
    "version": 2,
    "wins": 11, "losses": 0, "streak": 11, "best_streak": 11,
    "recent_results": [
        {"result":"win","source":"migration","ts":0.0,"prev_streak":0,"prev_best":0},
        {"result":"win","source":"migration","ts":0.0,"prev_streak":1,"prev_best":1},
        {"result":"win","source":"auto","ts":123.0,"prev_streak":10,"prev_best":10},
    ],
}
write(m.path, v2_chain)
s=m.snapshot()
assert (s["version"],s["wins"],s["streak"])==(3,11,11)
assert len(s["recent_results"])==1 and s["recent_results"][0]["source"]=="auto"
s,removed=m.undo()
assert removed is not None
assert (s["wins"],s["streak"],s["best_streak"])==(10,10,10)
_,removed2=m.undo()
assert removed2 is None
print("v11/v12 migration chain Undo safety: OK")

# Missing main must recover from valid backup, not silently reset to zero.
d=Path(tempfile.mkdtemp());m=StatsManager(d)
write(m.bak,v3(wins=42,losses=7,streak=3,best=9))
s=m.snapshot()
assert (s["wins"],s["losses"],s["streak"])==(42,7,3)
assert m.path.exists()
print("missing main -> backup restore: OK")


for bad_main in (
    {},
    {"foo":"bar"},
    {"version":"3","wins":10,"losses":0,"streak":0,"best_streak":0,"recent_results":[]},
    {"version":999,"wins":10,"losses":0,"streak":0,"best_streak":0,"recent_results":[]},
):
    d=Path(tempfile.mkdtemp());m=StatsManager(d)
    write(m.path,bad_main)
    write(m.bak,v3(wins=42,losses=7,streak=2,best=8))
    s=m.snapshot()
    assert (s["wins"],s["losses"],s["streak"])==(42,7,2)
print("arbitrary/unknown stats main -> backup recovery: OK")

print("\nAll StatsManager tests passed.")


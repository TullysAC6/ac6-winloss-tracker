import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from event_bus import EventBus

# Same boot replays only events after Last-Event-ID.
b = EventBus(history_size=10, boot_id="bootA")
r1=b.publish("stats",{"n":1}); r2=b.publish("stats",{"n":2})
c,replay,snaps=b.register_with_snapshots(r1["id"],lambda:[("stats",{"x":1})])
assert [r["seq"] for r in replay]==[2]
b.unregister(c)
print("same-boot replay boundary: OK")

# Old boot ID causes replay of retained events from the new generation.
b2=EventBus(history_size=10,boot_id="bootB")
b2.publish("stats",{"n":10});b2.publish("stats",{"n":11})
c,replay,_=b2.register_with_snapshots("bootA:500",lambda:[])
assert [r["seq"] for r in replay]==[1,2]
b2.unregister(c)
print("boot-epoch mismatch replay: OK")

# Initial connection without Last-Event-ID does not replay historical events.
c,replay,_=b2.register_with_snapshots("",lambda:[])
assert replay==[]
b2.unregister(c)
print("initial connection avoids stale replay: OK")

# Overflow marks the connection for forced reconnect instead of silently skipping.
b3=EventBus(history_size=20,client_queue_size=1,boot_id="bootC")
c,_,_=b3.register_with_snapshots("",lambda:[])
b3.publish("stats",{"n":1}); b3.publish("stats",{"n":2})
assert c.overflow.is_set()
assert b3.drop_count>=1
b3.unregister(c)
print("overflow forces reconnect flag: OK")

print("\nAll EventBus tests passed.")

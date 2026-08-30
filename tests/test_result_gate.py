import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from result_gate import ResultGate

g=ResultGate()
assert g.try_accept(5.0,now=100.0) is True   # subsequent duplicate
assert g.try_accept(5.0,now=101.0) is False  # duplicate during same banner
assert g.try_accept(5.0,now=104.9) is False
assert g.try_accept(5.0,now=105.0) is True
print("duplicate cooldown gate: OK")

g=ResultGate()
assert g.try_accept(5.0,now=200.0) is True   # prior accepted result
assert g.try_accept(5.0,now=201.0) is False  # subsequent duplicate
print("reverse-source-independent duplicate gate: OK")

g.clear_for_manual_correction()
assert g.try_accept(5.0,now=201.1) is True
print("undo correction gate reset: OK")


g=ResultGate()
assert g.try_accept(5.0,now=0.1) is True
print("no artificial initial lockout: OK")

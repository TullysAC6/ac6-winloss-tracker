import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import runtime_policy


def status(version, releaselevel="final", gil_disabled=0):
    return runtime_policy.evaluate_runtime(
        version, releaselevel=releaselevel, gil_disabled=gil_disabled
    )


for version in ((3, 14, 7), (3, 14, 8), (3, 14, 99)):
    result = status(version)
    assert result.supported and result.role == "preferred"

for version in ((3, 13, 15), (3, 13, 16), (3, 13, 99)):
    result = status(version)
    assert result.supported and result.role == "fallback"

for version in (
    (3, 14, 6), (3, 13, 14), (3, 12, 10), (3, 10, 99), (3, 15, 0)
):
    assert not status(version).supported

assert not status((3, 14, 7), releaselevel="candidate").supported
assert not status((3, 14, 7), releaselevel="beta").supported
assert not status((3, 14, 7), gil_disabled=1).supported
assert not status((3, 14, 7), gil_disabled="unknown").supported


def select_supported(candidates):
    accepted = [item for item in candidates if item[1].supported]
    if not accepted:
        return None
    return max(
        accepted,
        key=lambda item: (
            1 if item[1].role == "preferred" else 0,
            tuple(int(part) for part in item[1].version.split(".")),
        ),
    )[0]


cases = [
    (["3.12.10"], None),
    (["3.12.10", "3.14.7"], "3.14.7"),
    (["3.13.15", "3.14.7"], "3.14.7"),
    (["3.13.16"], "3.13.16"),
    (["3.14.6", "3.13.15"], "3.13.15"),
    (["3.15.0", "3.14.7"], "3.14.7"),
    (["3.15.0"], None),
    (["3.14.7", "3.14.8"], "3.14.8"),
]
for versions, expected in cases:
    candidates = []
    for version in versions:
        parts = tuple(int(part) for part in version.split("."))
        candidates.append((version, status(parts)))
    assert select_supported(candidates) == expected

policy = json.loads((ROOT / "runtime-policy.json").read_text(encoding="utf-8"))
assert policy == runtime_policy.POLICY
assert policy["policy_version"] == 1
assert policy["preferred"]["winget_package_id"] == "Python.Python.3.14"

current = runtime_policy.current_runtime_status()
if sys.version_info[:2] == (3, 12):
    assert not current.supported and current.role == "unsupported"
elif sys.version_info[:2] == (3, 13):
    assert current.supported and current.role == "fallback"
elif sys.version_info[:2] == (3, 14):
    if sys.version_info.micro >= policy["preferred"]["minimum_patch"]:
        assert current.supported and current.role == "preferred"
    else:
        assert not current.supported

print("Runtime policy floors, release level, GIL mode, and selection: OK")

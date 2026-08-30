import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME_PATH = ROOT / ".runtime.json"

ACTIONS = {
    "undo": "/api/stats/undo",
    "reset": "/api/stats/reset",
}

if len(sys.argv) != 2 or sys.argv[1] not in ACTIONS:
    raise SystemExit("usage: control.py undo|reset")

if not RUNTIME_PATH.exists():
    raise SystemExit("server is not running (.runtime.json not found)")

try:
    runtime = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
    port = runtime["port"]
    token = runtime["token"]
except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
    raise SystemExit(f"invalid .runtime.json: {e}")

if type(port) is not int or not 1024 <= port <= 65535:
    raise SystemExit("invalid runtime port")
if not isinstance(token, str) or len(token) < 32:
    raise SystemExit("invalid runtime token")

url = f"http://127.0.0.1:{port}{ACTIONS[sys.argv[1]]}"
req = urllib.request.Request(
    url,
    data=b"",
    method="POST",
    headers={"X-Control-Token": token},
)

try:
    with urllib.request.urlopen(req, timeout=5) as r:
        print(r.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    print(e.read().decode("utf-8", errors="replace"))
    raise SystemExit(1)
except OSError as e:
    raise SystemExit(f"control request failed: {e}")

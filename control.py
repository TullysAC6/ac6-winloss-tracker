"""Authenticated localhost controls. Confirmation always names an exact event."""
import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("undo", "reset", "metadata-on", "metadata-off",
                        "metadata-acquire", "metadata-status", "metadata-cancel", "metadata-confirm"))
    parser.add_argument("--runtime", type=Path, help="isolated Tracker .runtime.json")
    parser.add_argument("--request-id")
    parser.add_argument("--event-id")
    parser.add_argument("--same-match", action="store_true",
                        help="attest this header belonged to this exact match; no intervening match/mode change")
    parser.add_argument("--delay", type=float, default=3.0,
                        help="acquire only: seconds to return focus to AC6 (0..10)")
    args = parser.parse_args(argv)
    if not 0 <= args.delay <= 10:
        parser.error("delay must be 0..10 seconds")
    if args.action == "metadata-confirm" and not (args.request_id and args.event_id and args.same_match):
        parser.error("confirm requires --request-id, --event-id and explicit --same-match")
    if args.action != "metadata-confirm" and (args.request_id or args.event_id or args.same_match):
        parser.error("confirmation fields are only valid for metadata-confirm")
    if args.runtime is None:
        from app_paths import data_dir
        args.runtime = data_dir() / ".runtime.json"
    try:
        runtime = json.loads(args.runtime.read_text(encoding="utf-8"))
        port, token = runtime["port"], runtime["token"]
        if (type(port) is not int or not 1024 <= port <= 65535
                or not isinstance(token, str) or not 32 <= len(token) <= 256
                or type(runtime.get("pid")) is not int or runtime["pid"] <= 0):
            raise ValueError("invalid runtime identity/port/token")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(f"invalid/unavailable runtime file: {error}")
    if args.action in ("undo", "reset"):
        path, body = "/api/stats/" + args.action, {}
    else:
        path = "/api/metadata"
        action = args.action.removeprefix("metadata-")
        body = {"action": action}
        if action in ("on", "off"):
            body = {"action": "enable", "enabled": action == "on"}
        elif action == "confirm":
            body.update(request_id=args.request_id, event_id=args.event_id, same_match=args.same_match)
        elif action == "acquire" and args.delay:
            print(f"Return to the RANK MATCH: SINGLE lobby within {args.delay:g} seconds.", flush=True)
            time.sleep(args.delay)  # User-focus affordance, not a correctness/watchdog wait.
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(), method="POST",
        headers={"X-Control-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            print(response.read(8193).decode("utf-8"))
    except urllib.error.HTTPError as error:
        print(error.read(8193).decode("utf-8", errors="replace"))
        raise SystemExit(1)
    except OSError as error:
        raise SystemExit(f"control request failed: {error}")


if __name__ == "__main__":
    main()

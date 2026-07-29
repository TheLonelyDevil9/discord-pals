#!/usr/bin/env python3
"""Boot the dashboard with authentication on and check the access boundary.

The unit tests use Flask ' s test client, which never binds a socket. This runs the
real Waitress server the deployment uses, so it also catches a login gate that
only works in-process.

Exits non-zero if the liveness probe is unreachable or if any dashboard page or
API answers without a login.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PORT = int(os.getenv("SMOKE_PORT", "8731"))
GUARDED_PATHS = ("/", "/api/version", "/api/config", "/config", "/logs")


def main() -> int:
    os.environ["DASHBOARD_USER"] = "smoke"
    os.environ["DASHBOARD_PASS"] = "smoke-password"

    from dashboard import start_dashboard

    if start_dashboard(bots=[], host="127.0.0.1", port=PORT) is None:
        print("FAIL: dashboard thread did not start")
        return 1

    base = f"http://127.0.0.1:{PORT}"

    for _ in range(50):
        try:
            urllib.request.urlopen(f"{base}/healthz", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    else:
        print("FAIL: dashboard never became reachable")
        return 1

    with urllib.request.urlopen(f"{base}/healthz", timeout=5) as response:
        body = response.read().decode().strip()
        if response.status != 200:
            print(f"FAIL: /healthz returned {response.status}")
            return 1
    print(f"ok   /healthz -> 200 {body}")

    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
    for path in GUARDED_PATHS:
        with opener.open(urllib.request.Request(f"{base}{path}"), timeout=5) as response:
            landed = response.geturl()
        if "/login" not in landed:
            print(f"FAIL: {path} served without a login; landed on {landed}")
            return 1
        print(f"ok   {path} -> login required")

    print("dashboard smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

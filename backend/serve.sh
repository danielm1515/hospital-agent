#!/bin/sh
# The backend's development server, with a watchdog (docker-compose.yml `command`).
#
# uvicorn --reload runs the app in a worker under a reloader. If that worker dies, the reloader
# starts a new one only when a file changes - otherwise it keeps the port, answers nothing, and
# the UI waits on "טוען…" forever (seen on 2026-10-03: a worker left as a zombie). The container
# would stay "up" all along, so Docker's restart policy would never act.
#
# The watchdog asks /health every WATCHDOG_INTERVAL seconds; after WATCHDOG_FAILURES failures in
# a row it stops uvicorn and exits non-zero, and `restart: unless-stopped` brings the container
# back. A slow startup (migrations, the first import) gets WATCHDOG_GRACE seconds first.
set -u

WATCHDOG_INTERVAL="${WATCHDOG_INTERVAL:-10}"
WATCHDOG_FAILURES="${WATCHDOG_FAILURES:-3}"
WATCHDOG_GRACE="${WATCHDOG_GRACE:-30}"

alembic upgrade head || exit 1

uvicorn hospital_agent.api.app:app --host 0.0.0.0 --port 8000 --reload &
UVICORN=$!

healthy() {
  python - <<'PY'
import sys, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception:
    sys.exit(1)
PY
}

sleep "$WATCHDOG_GRACE"
failures=0
while kill -0 "$UVICORN" 2>/dev/null; do
  if healthy; then
    failures=0
  else
    failures=$((failures + 1))
    echo "watchdog: /health failed ($failures/$WATCHDOG_FAILURES)"
    if [ "$failures" -ge "$WATCHDOG_FAILURES" ]; then
      echo "watchdog: the server stopped answering - exiting so Docker restarts the container"
      kill "$UVICORN" 2>/dev/null
      sleep 5
      kill -9 "$UVICORN" 2>/dev/null
      exit 1
    fi
  fi
  sleep "$WATCHDOG_INTERVAL"
done

echo "watchdog: uvicorn exited - exiting so Docker restarts the container"
exit 1

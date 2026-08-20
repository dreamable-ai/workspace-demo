#!/usr/bin/env bash

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(dirname -- "$SCRIPT_DIR")
GATEWAY_BIN="$PROJECT_ROOT/.venv/bin/workspace-gateway"
PID_FILE="$PROJECT_ROOT/.workspace-gateway.pid"

if [ ! -x "$GATEWAY_BIN" ]; then
    echo "Workspace Gateway is not installed. Run: .venv/bin/pip install -e ." >&2
    exit 1
fi

is_running() {
    [ -f "$PID_FILE" ] || return 1
    PID=$(sed -n '1p' "$PID_FILE")
    case "$PID" in
        ''|*[!0-9]*) return 1 ;;
    esac
    kill -0 "$PID" 2>/dev/null
}

start() {
    if is_running; then
        echo "Workspace Gateway is already running (PID $PID)."
        return 0
    fi
    rm -f "$PID_FILE"

    cd "$PROJECT_ROOT"
    nohup "$GATEWAY_BIN" >/dev/null 2>&1 &
    PID=$!
    echo "$PID" >"$PID_FILE"

    sleep 1
    if ! kill -0 "$PID" 2>/dev/null; then
        rm -f "$PID_FILE"
        echo "Workspace Gateway failed to start. Check the application log." >&2
        exit 1
    fi
    echo "Workspace Gateway started in the background (PID $PID)."
}

stop_for_restart() {
    if ! is_running; then
        rm -f "$PID_FILE"
        return 0
    fi

    kill "$PID"
    ATTEMPT=0
    while kill -0 "$PID" 2>/dev/null && [ "$ATTEMPT" -lt 50 ]; do
        sleep 0.2
        ATTEMPT=$((ATTEMPT + 1))
    done
    if kill -0 "$PID" 2>/dev/null; then
        echo "Workspace Gateway did not stop within 10 seconds (PID $PID)." >&2
        exit 1
    fi
    rm -f "$PID_FILE"
}

COMMAND=${1:-start}
case "$COMMAND" in
    start)
        start
        ;;
    restart)
        stop_for_restart
        start
        ;;
    *)
        echo "Usage: $0 {start|restart}" >&2
        exit 2
        ;;
esac

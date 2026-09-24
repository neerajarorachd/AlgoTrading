#!/usr/bin/env bash
# Waits for SQL Server to actually accept TCP connections on 127.0.0.1:1433
# before letting the main app start -- closes a real boot-time race: the VM
# boots both mssql-server.service (system-level) and algotrading-backend
# (user-level) around the same time with no ordering guarantee between the
# two scopes, and SQL Server can take real time to finish initializing.
# Fails loudly (non-zero exit) after a generous timeout rather than hanging
# forever, so a genuinely broken SQL Server still surfaces as a clear
# systemd failure instead of a silent stall.
set -euo pipefail

HOST=127.0.0.1
PORT=1433
TIMEOUT_SECONDS=120
elapsed=0

while ! (exec 3<>"/dev/tcp/${HOST}/${PORT}") 2>/dev/null; do
  if [ "$elapsed" -ge "$TIMEOUT_SECONDS" ]; then
    echo "wait_for_sqlserver: timed out after ${TIMEOUT_SECONDS}s waiting for ${HOST}:${PORT}" >&2
    exit 1
  fi
  sleep 2
  elapsed=$((elapsed + 2))
done
exec 3<&- 3>&- 2>/dev/null || true

echo "wait_for_sqlserver: SQL Server reachable after ${elapsed}s"

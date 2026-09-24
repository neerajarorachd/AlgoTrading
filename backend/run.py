"""Entrypoint for both local dev and the VM's systemd service (added
2026-09-16 — see ops/algotrading-backend.service). Run with:
    python run.py (from backend/, with the venv active)

Flask's plain app.run() doesn't support WebSocket upgrades — Flask-SocketIO
needs socketio.run(app, ...) instead so the WS transport is actually wired up.

Host/port default to 127.0.0.1:5000 (loopback-only, same security posture as
SQL Server itself — CLAUDE.md: "Never expose ... directly to the public
internet"/"Use SSH tunnel for local access") whether run locally or on the
VM; override via HOST/PORT env vars only if a real reason to bind
differently comes up (none has yet — viewing the app from a local machine
when it's running on the VM uses the same SSH tunnel pattern already used
for SQL Server: `ssh -L 5000:127.0.0.1:5000 trading-server`).
"""
import logging
import os

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from app import create_app
from api.ws_live import socketio

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    # allow_unsafe_werkzeug: Flask-SocketIO 5.x refuses to run its bundled
    # Werkzeug server outside debug mode without this — the warning is about
    # exposing it to real internet traffic at scale, which doesn't apply
    # here (loopback-only, reached only through the same SSH tunnel pattern
    # already used for SQL Server, single low-volume internal service).
    # async_mode stays "threading" (see api/ws_live.py's own comment) rather
    # than switching to eventlet/gevent to fix this "properly," since that
    # would reintroduce the exact monkey-patch conflict with DhanBroker's
    # own real-OS-threads WS client that "threading" mode was chosen to avoid.
    socketio.run(app, host=host, port=port, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)

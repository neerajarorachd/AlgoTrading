"""Local dev entrypoint. Run with: python run.py (from backend/, with .venv active).

Flask's plain app.run() doesn't support WebSocket upgrades — Flask-SocketIO
needs socketio.run(app, ...) instead so the WS transport is actually wired up.
"""
from dotenv import load_dotenv

load_dotenv()

from app import create_app
from api.ws_live import socketio

app = create_app()

if __name__ == "__main__":
    socketio.run(app, host="127.0.0.1", port=5000, debug=False, use_reloader=False)

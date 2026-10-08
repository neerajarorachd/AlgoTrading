"""Live-feed health for the UI -- whether the broker WebSocket is up, when it
last delivered anything, how many instruments it's carrying. Added
2026-10-06 after the feed was found silently disconnected for a whole
session (LTP "—" everywhere, no error anywhere); see DhanBroker.feed_status."""
from flask import Blueprint, current_app, jsonify

feed_bp = Blueprint("feed", __name__)


@feed_bp.get("/api/feed/status")
def get_feed_status():
    replay = current_app.extensions.get("replay_feed")
    if replay is not None:
        # replay mode (replay_feed.py) -- the UI must show this unmistakably
        return jsonify(replay.status())
    broker = current_app.extensions.get("feed_broker")
    status = getattr(broker, "feed_status", None)
    if status is None:
        # a broker without health reporting (test fakes, other adapters)
        return jsonify({"available": False})
    return jsonify({"available": True, **status()})

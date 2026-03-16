"""
API Server for Emily AI Journal PWA
Runs on the same port as the health check (8000), adding JSON API routes.
"""
import os
import json
import hashlib
import secrets
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from datetime import datetime

import pytz
import certifi
from pymongo import MongoClient

logger = logging.getLogger(__name__)
EAT_ZONE = pytz.timezone('Africa/Nairobi')

# ── MongoDB (separate connection for API thread) ──
_api_db = None
try:
    _api_client = MongoClient(
        os.getenv("MONGO_URI"),
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=5000,
    )
    _api_client.admin.command('ping')
    _api_db = _api_client["emily_brain_db"]
    logger.info("API server connected to MongoDB")
except Exception as e:
    logger.error(f"API MongoDB error: {e}")


# ══════════════════════════════════════════════
# AUTH: Token management
# ══════════════════════════════════════════════
def generate_app_token(user_id, username):
    """Generate a token for PWA auth. Returns token string."""
    if _api_db is None:
        return None
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    _api_db["app_tokens"].update_one(
        {"user_id": str(user_id)},
        {"$set": {
            "user_id": str(user_id),
            "username": username,
            "token_hash": token_hash,
            "created_at": datetime.now(EAT_ZONE),
        }},
        upsert=True,
    )
    return token


def verify_token(token):
    """Verify a PWA token. Returns user_id or None."""
    if _api_db is None or not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    doc = _api_db["app_tokens"].find_one({"token_hash": token_hash})
    if doc:
        return doc["user_id"]
    return None


# ══════════════════════════════════════════════
# JOURNAL API FUNCTIONS (thread-safe, own connection)
# ══════════════════════════════════════════════
MOOD_SCALE = {
    1: ("😢", "terrible"),
    2: ("😔", "rough"),
    3: ("😐", "okay"),
    4: ("😊", "good"),
    5: ("🤩", "amazing"),
}

MOOD_KEYWORDS = {
    5: ["amazing", "incredible", "best day", "promoted", "wonderful", "fantastic",
        "blessed", "thrilled", "ecstatic", "overjoyed"],
    4: ["great", "good day", "happy", "proud", "grateful", "excited", "fun",
        "accomplished", "productive", "awesome", "peaceful"],
    3: ["okay", "fine", "alright", "normal", "average", "meh", "not bad", "so-so"],
    2: ["tough", "hard", "difficult", "stressed", "tired", "frustrated", "annoyed",
        "disappointed", "lonely", "anxious", "worried", "sad", "rough"],
    1: ["terrible", "awful", "worst", "crying", "depressed", "heartbroken",
        "devastated", "hopeless", "miserable"],
}


def _detect_mood(text):
    text_lower = text.lower()
    for score in [1, 5, 2, 4, 3]:
        for keyword in MOOD_KEYWORDS[score]:
            if keyword in text_lower:
                return score
    return 3


def api_add_entry(user_id, text, mood_score=None):
    """Add journal entry via API."""
    if _api_db is None:
        return None
    if mood_score is None:
        mood_score = _detect_mood(text)
    mood_score = max(1, min(5, mood_score))
    emoji, label = MOOD_SCALE.get(mood_score, ("😐", "okay"))

    now = datetime.now(EAT_ZONE)
    entry = {
        "user_id": str(user_id),
        "text": text,
        "mood_score": mood_score,
        "mood_emoji": emoji,
        "mood_label": label,
        "tags": [],
        "date": now,
        "date_str": now.strftime("%Y-%m-%d"),
        "time_str": now.strftime("%I:%M %p"),
        "day_name": now.strftime("%A"),
        "source": "app",
    }
    _api_db["journal"].insert_one(entry)
    entry.pop("_id", None)
    entry["date"] = entry["date"].isoformat()
    return entry


def api_get_entries(user_id, days=14, limit=20):
    """Get journal entries via API."""
    if _api_db is None:
        return []
    from datetime import timedelta
    cutoff = datetime.now(EAT_ZONE) - timedelta(days=days)
    entries = list(_api_db["journal"].find({
        "user_id": str(user_id),
        "date": {"$gte": cutoff},
    }).sort("date", -1).limit(limit))

    for e in entries:
        e["_id"] = str(e["_id"])
        if isinstance(e.get("date"), datetime):
            e["date"] = e["date"].isoformat()
    return entries


def api_get_mood_trend(user_id, days=14):
    """Get mood trend data via API."""
    if _api_db is None:
        return []
    from datetime import timedelta
    cutoff = datetime.now(EAT_ZONE) - timedelta(days=days)
    entries = list(_api_db["journal"].find({
        "user_id": str(user_id),
        "date": {"$gte": cutoff},
    }).sort("date", 1))

    daily = {}
    for e in entries:
        day = e.get("date_str", "")
        if day not in daily:
            daily[day] = []
        daily[day].append(e.get("mood_score", 3))

    return [
        {"date": day, "avg_mood": round(sum(s)/len(s), 1), "entries": len(s)}
        for day, s in sorted(daily.items())
    ]


def api_get_stats(user_id, days=30):
    """Get mood stats via API."""
    if _api_db is None:
        return None
    from datetime import timedelta
    cutoff = datetime.now(EAT_ZONE) - timedelta(days=days)
    entries = list(_api_db["journal"].find({
        "user_id": str(user_id),
        "date": {"$gte": cutoff},
    }))
    if not entries:
        return None

    scores = [e.get("mood_score", 3) for e in entries]
    dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for s in scores:
        dist[s] = dist.get(s, 0) + 1

    # Streak
    dates = sorted(set(e.get("date_str", "") for e in entries), reverse=True)
    today = datetime.now(EAT_ZONE).strftime("%Y-%m-%d")
    yesterday = (datetime.now(EAT_ZONE) - timedelta(days=1)).strftime("%Y-%m-%d")
    streak = 0
    if dates and (dates[0] == today or dates[0] == yesterday):
        streak = 1
        for i in range(1, len(dates)):
            prev = datetime.strptime(dates[i-1], "%Y-%m-%d")
            curr = datetime.strptime(dates[i], "%Y-%m-%d")
            if (prev - curr).days == 1:
                streak += 1
            else:
                break

    return {
        "total_entries": len(entries),
        "avg_mood": round(sum(scores)/len(scores), 1),
        "mood_distribution": dist,
        "streak": streak,
    }


def api_quick_mood(user_id, mood_score):
    """Quick mood check-in (just a score, no text)."""
    emoji, label = MOOD_SCALE.get(mood_score, ("😐", "okay"))
    return api_add_entry(user_id, f"Quick check-in: feeling {label}", mood_score)


# ══════════════════════════════════════════════
# NOTES API FUNCTIONS
# ══════════════════════════════════════════════
def api_create_note(user_id, title, body="", color="#f59e0b"):
    """Create a new note."""
    if _api_db is None:
        return None
    import uuid
    now = datetime.now(EAT_ZONE)
    note_id = str(uuid.uuid4())[:12]
    note = {
        "user_id": str(user_id),
        "note_id": note_id,
        "title": title,
        "body": body,
        "color": color,
        "created_at": now,
        "updated_at": now,
        "created_str": now.strftime("%b %d, %I:%M %p"),
        "updated_str": now.strftime("%b %d, %I:%M %p"),
    }
    _api_db["notes"].insert_one(note)
    note.pop("_id", None)
    note["_id"] = note_id
    note["created_at"] = now.isoformat()
    note["updated_at"] = now.isoformat()
    return note


def api_get_notes(user_id):
    """Get all notes for a user, newest first."""
    if _api_db is None:
        return []
    notes = list(_api_db["notes"].find(
        {"user_id": str(user_id)}
    ).sort("updated_at", -1))
    for n in notes:
        n["_id"] = n.get("note_id", str(n["_id"]))
        if isinstance(n.get("created_at"), datetime):
            n["created_at"] = n["created_at"].isoformat()
        if isinstance(n.get("updated_at"), datetime):
            n["updated_at"] = n["updated_at"].isoformat()
    return notes


def api_update_note(user_id, note_id, title=None, body=None, color=None):
    """Update an existing note."""
    if _api_db is None:
        return None
    try:
        update = {"updated_at": datetime.now(EAT_ZONE)}
        update["updated_str"] = update["updated_at"].strftime("%b %d, %I:%M %p")
        if title is not None:
            update["title"] = title
        if body is not None:
            update["body"] = body
        if color is not None:
            update["color"] = color

        result = _api_db["notes"].update_one(
            {"note_id": note_id, "user_id": str(user_id)},
            {"$set": update}
        )
        return {"updated": result.modified_count > 0}
    except Exception as e:
        logger.error(f"Update note error: {e}")
        return None


def api_delete_note(user_id, note_id):
    """Delete a note."""
    if _api_db is None:
        return False
    try:
        result = _api_db["notes"].delete_one(
            {"note_id": note_id, "user_id": str(user_id)}
        )
        return result.deleted_count > 0
    except Exception as e:
        logger.error(f"Delete note error: {e}")
        return False


# ══════════════════════════════════════════════
# HTTP API HANDLER
# ══════════════════════════════════════════════
class EmilyAPIHandler(BaseHTTPRequestHandler):
    """Handles both health checks and journal API requests."""

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def _send_error(self, message, status=400):
        self._send_json({"error": message}, status)

    def _get_user(self):
        """Extract user_id from Authorization header."""
        auth = self.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            token = auth[7:]
            return verify_token(token)
        return None

    def _read_body(self):
        """Read and parse JSON request body."""
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length == 0:
                return {}
            body = self.rfile.read(length)
            return json.loads(body)
        except Exception:
            return {}

    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Connection', 'close')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # Health check
        if path == "/" or path == "/health":
            self._send_json({"status": "ok", "service": "emily-ai"})
            return

        # ── AUTH REQUIRED ROUTES ──
        user_id = self._get_user()
        if not user_id and path.startswith("/api/"):
            self._send_error("Unauthorized", 401)
            return

        # Get journal entries
        if path == "/api/journal/entries":
            days = int(params.get("days", [14])[0])
            limit = int(params.get("limit", [20])[0])
            entries = api_get_entries(user_id, days=days, limit=limit)
            self._send_json({"entries": entries})

        # Get mood trend
        elif path == "/api/journal/mood-trend":
            days = int(params.get("days", [14])[0])
            trend = api_get_mood_trend(user_id, days=days)
            self._send_json({"trend": trend})

        # Get stats
        elif path == "/api/journal/stats":
            days = int(params.get("days", [30])[0])
            stats = api_get_stats(user_id, days=days)
            self._send_json({"stats": stats or {}})

        # Get notes
        elif path == "/api/notes":
            notes = api_get_notes(user_id)
            self._send_json({"notes": notes})

        else:
            self._send_error("Not found", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # ── AUTH REQUIRED ──
        user_id = self._get_user()
        if not user_id and path.startswith("/api/"):
            self._send_error("Unauthorized", 401)
            return

        body = self._read_body()

        # Add journal entry
        if path == "/api/journal/entry":
            text = body.get("text", "").strip()
            mood = body.get("mood_score")
            if not text and mood:
                # Quick mood check-in
                entry = api_quick_mood(user_id, int(mood))
            elif text:
                entry = api_add_entry(user_id, text, int(mood) if mood else None)
            else:
                self._send_error("Text or mood_score required")
                return

            if entry:
                self._send_json({"entry": entry}, 201)
            else:
                self._send_error("Failed to save entry", 500)

        # Quick mood
        elif path == "/api/journal/quick-mood":
            mood = body.get("mood_score")
            if not mood:
                self._send_error("mood_score required")
                return
            entry = api_quick_mood(user_id, int(mood))
            if entry:
                self._send_json({"entry": entry}, 201)
            else:
                self._send_error("Failed to save", 500)

        # Create note
        elif path == "/api/notes":
            title = body.get("title", "Untitled")
            note_body = body.get("body", "")
            color = body.get("color", "#f59e0b")
            note = api_create_note(user_id, title, note_body, color)
            if note:
                self._send_json({"note": note}, 201)
            else:
                self._send_error("Failed to create note", 500)

        # Update note
        elif path == "/api/notes/update":
            note_id = body.get("id")
            if not note_id:
                self._send_error("Note id required")
                return
            note = api_update_note(user_id, note_id, body.get("title"), body.get("body"), body.get("color"))
            if note:
                self._send_json({"note": note})
            else:
                self._send_error("Failed to update", 500)

        # Delete note
        elif path == "/api/notes/delete":
            note_id = body.get("id")
            if not note_id:
                self._send_error("Note id required")
                return
            if api_delete_note(user_id, note_id):
                self._send_json({"deleted": True})
            else:
                self._send_error("Failed to delete", 500)

        else:
            self._send_error("Not found", 404)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Connection', 'close')
        self.end_headers()

    def log_message(self, format, *args):
        # Only log API calls, not health checks
        path = args[0].split()[1] if args else ""
        if "/api/" in path:
            logger.info(f"API: {args[0]}")


def run_api_server(ready_event):
    """Start the API server (replaces health check server)."""
    port = int(os.getenv("PORT", 8000))
    server = HTTPServer(('0.0.0.0', port), EmilyAPIHandler)
    logger.info(f"Emily API server LIVE on port {port}")
    ready_event.set()
    server.serve_forever()

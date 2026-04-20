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
from datetime import datetime, timedelta

import pytz
import certifi
from pymongo import MongoClient

logger = logging.getLogger(__name__)
EAT_ZONE = pytz.timezone('Africa/Nairobi')

# CORS — restrict to journal app origin (set JOURNAL_APP_URL in env)
# Falls back to '*' if not set (for local dev)
ALLOWED_ORIGIN = os.getenv("JOURNAL_APP_URL", "*")

# ══════════════════════════════════════════════
# OBSERVABILITY — shared bot status (updated by main.py)
# ══════════════════════════════════════════════
bot_status = {
    "started_at": None,
    "last_message_at": None,
    "messages_processed": 0,
    "commands_processed": 0,
    "errors_count": 0,
    "task_health": {},  # {task_name: {"last_run": datetime, "errors": int}}
}


def update_task_health(task_name, success=True):
    """Called from main.py background tasks to record health."""
    now = datetime.now(EAT_ZONE)
    if task_name not in bot_status["task_health"]:
        bot_status["task_health"][task_name] = {"last_run": None, "errors": 0, "runs": 0}
    entry = bot_status["task_health"][task_name]
    entry["last_run"] = now.isoformat()
    entry["runs"] = entry.get("runs", 0) + 1
    if not success:
        entry["errors"] = entry.get("errors", 0) + 1


def _safe_int(params, key, default, min_val=1, max_val=365):
    """Parse a query-string integer safely. Returns default on anything invalid.

    Previously `int(params.get("days", [14])[0])` raised ValueError on ?days=foo
    and crashed the request with a 500. This clamps to sensible bounds too.
    """
    try:
        raw = params.get(key, [default])[0]
        n = int(raw)
        if n < min_val:
            return min_val
        if n > max_val:
            return max_val
        return n
    except (ValueError, TypeError, IndexError):
        return default

# ── MongoDB (auto-reconnecting for API thread) ──
_api_client = None
_api_db = None

def _get_db():
    """Get MongoDB database, reconnecting if needed."""
    global _api_client, _api_db
    if _api_db is not None:
        try:
            _api_client.admin.command('ping')
            return _api_db
        except Exception:
            logger.warning("API MongoDB connection lost, reconnecting...")
            _api_client = None
            _api_db = None

    try:
        _api_client = MongoClient(
            os.getenv("MONGO_URI"),
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=10000,
            retryWrites=True,
            retryReads=True,
        )
        _api_client.admin.command('ping')
        _api_db = _api_client["emily_brain_db"]
        logger.info("API server connected to MongoDB")
        return _api_db
    except Exception as e:
        logger.error(f"API MongoDB reconnect failed: {e}")
        _api_client = None
        _api_db = None
        return None


# ══════════════════════════════════════════════
# AUTH: Token management
# ══════════════════════════════════════════════
TOKEN_EXPIRY_DAYS = 30  # Tokens expire after 30 days


def generate_app_token(user_id, username):
    """Generate a token for PWA auth. Returns token string."""
    db = _get_db()
    if db is None:
        return None
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(EAT_ZONE)
    db["app_tokens"].update_one(
        {"user_id": str(user_id)},
        {"$set": {
            "user_id": str(user_id),
            "username": username,
            "token_hash": token_hash,
            "created_at": now,
            "expires_at": now + timedelta(days=TOKEN_EXPIRY_DAYS),
            "last_used_at": now,
        }},
        upsert=True,
    )
    return token


def verify_token(token):
    """Verify a PWA token. Returns user_id or None. Rejects expired tokens."""
    db = _get_db()
    if db is None or not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    doc = db["app_tokens"].find_one({"token_hash": token_hash})
    if not doc:
        return None

    # Check expiry
    expires_at = doc.get("expires_at")
    now = datetime.now(EAT_ZONE)
    if expires_at and now > expires_at:
        logger.info(f"Token expired for user {doc.get('user_id')}")
        return None

    # Update last_used_at
    try:
        db["app_tokens"].update_one(
            {"token_hash": token_hash},
            {"$set": {"last_used_at": now}}
        )
    except Exception:
        pass

    return doc["user_id"]


def revoke_token(user_id):
    """Revoke a user's app token."""
    db = _get_db()
    if db is None:
        return False
    try:
        result = db["app_tokens"].delete_one({"user_id": str(user_id)})
        return result.deleted_count > 0
    except Exception:
        return False


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


def api_add_entry(user_id, text, mood_score=None, tags=None, photos=None):
    """Add journal entry via API."""
    db = _get_db()
    if db is None:
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
        "tags": tags or [],
        "photos": (photos or [])[:4],  # Max 4 photos
        "pinned": False,
        "date": now,
        "date_str": now.strftime("%Y-%m-%d"),
        "time_str": now.strftime("%I:%M %p"),
        "day_name": now.strftime("%A"),
        "source": "app",
    }
    db["journal"].insert_one(entry)
    entry.pop("_id", None)
    entry["date"] = entry["date"].isoformat()
    return entry


def api_get_entries(user_id, days=14, limit=20):
    """Get journal entries via API."""
    db = _get_db()
    if db is None:
        return []
    from datetime import timedelta
    cutoff = datetime.now(EAT_ZONE) - timedelta(days=days)
    entries = list(db["journal"].find({
        "user_id": str(user_id),
        "date": {"$gte": cutoff},
    }).sort("date", -1).limit(limit))

    for e in entries:
        e["_id"] = str(e["_id"])
        if isinstance(e.get("date"), datetime):
            e["date"] = e["date"].isoformat()
        # Ensure cross-platform fields exist (older Discord entries may lack these)
        e.setdefault("source", "discord")
        e.setdefault("pinned", False)
        e.setdefault("photos", [])
        e.setdefault("tags", [])
    return entries


def api_get_mood_trend(user_id, days=14):
    """Get mood trend data via API."""
    db = _get_db()
    if db is None:
        return []
    from datetime import timedelta
    cutoff = datetime.now(EAT_ZONE) - timedelta(days=days)
    entries = list(db["journal"].find({
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
    db = _get_db()
    if db is None:
        return None
    from datetime import timedelta
    cutoff = datetime.now(EAT_ZONE) - timedelta(days=days)
    entries = list(db["journal"].find({
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


def api_update_entry(user_id, entry_id, text=None, mood_score=None, tags=None, pinned=None):
    """Update a journal entry."""
    db = _get_db()
    if db is None:
        return None
    try:
        from bson import ObjectId
        update = {}
        if text is not None:
            update["text"] = text
        if mood_score is not None:
            mood_score = max(1, min(5, int(mood_score)))
            emoji, label = MOOD_SCALE.get(mood_score, ("😐", "okay"))
            update["mood_score"] = mood_score
            update["mood_emoji"] = emoji
            update["mood_label"] = label
        if tags is not None:
            update["tags"] = tags
        if pinned is not None:
            update["pinned"] = bool(pinned)
        if not update:
            return None
        result = db["journal"].update_one(
            {"_id": ObjectId(entry_id), "user_id": str(user_id)},
            {"$set": update}
        )
        return result.modified_count > 0
    except Exception as e:
        logger.error(f"Update entry error: {e}")
        return None


def api_delete_entry(user_id, entry_id):
    """Delete a journal entry."""
    db = _get_db()
    if db is None:
        return False
    try:
        from bson import ObjectId
        result = db["journal"].delete_one(
            {"_id": ObjectId(entry_id), "user_id": str(user_id)}
        )
        return result.deleted_count > 0
    except Exception as e:
        logger.error(f"Delete entry error: {e}")
        return False


# ══════════════════════════════════════════════
# GRATITUDE API
# ══════════════════════════════════════════════
def api_save_gratitude(user_id, items):
    """Save today's gratitude list."""
    db = _get_db()
    if db is None:
        return None
    now = datetime.now(EAT_ZONE)
    today = now.strftime("%Y-%m-%d")
    doc = {
        "user_id": str(user_id),
        "items": items[:3],
        "date_str": today,
        "date": now,
    }
    db["gratitude"].update_one(
        {"user_id": str(user_id), "date_str": today},
        {"$set": doc},
        upsert=True,
    )
    return doc


def api_get_gratitude(user_id):
    """Get today's gratitude entry."""
    db = _get_db()
    if db is None:
        return None
    today = datetime.now(EAT_ZONE).strftime("%Y-%m-%d")
    doc = db["gratitude"].find_one(
        {"user_id": str(user_id), "date_str": today},
        {"_id": 0}
    )
    if doc and isinstance(doc.get("date"), datetime):
        doc["date"] = doc["date"].isoformat()
    return doc


# ══════════════════════════════════════════════
# SLEEP API
# ══════════════════════════════════════════════
def api_save_sleep(user_id, quality, hours):
    """Save sleep data for today."""
    db = _get_db()
    if db is None:
        return None
    now = datetime.now(EAT_ZONE)
    today = now.strftime("%Y-%m-%d")
    doc = {
        "user_id": str(user_id),
        "quality": max(1, min(5, int(quality))),
        "hours": round(float(hours), 1),
        "date_str": today,
        "date": now,
    }
    db["sleep"].update_one(
        {"user_id": str(user_id), "date_str": today},
        {"$set": doc},
        upsert=True,
    )
    return doc


def api_get_sleep(user_id, days=7):
    """Get sleep data for recent days."""
    db = _get_db()
    if db is None:
        return []
    from datetime import timedelta
    cutoff = datetime.now(EAT_ZONE) - timedelta(days=days)
    entries = list(db["sleep"].find(
        {"user_id": str(user_id), "date": {"$gte": cutoff}},
        {"_id": 0}
    ).sort("date", -1))
    for e in entries:
        if isinstance(e.get("date"), datetime):
            e["date"] = e["date"].isoformat()
    return entries


# ══════════════════════════════════════════════
# NOTES API FUNCTIONS
# ══════════════════════════════════════════════
def api_create_note(user_id, title, body="", color="#f59e0b"):
    """Create a new note."""
    db = _get_db()
    if db is None:
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
    db["notes"].insert_one(note)
    note.pop("_id", None)
    note["_id"] = note_id
    note["created_at"] = now.isoformat()
    note["updated_at"] = now.isoformat()
    return note


def api_get_notes(user_id):
    """Get all notes for a user, newest first."""
    db = _get_db()
    if db is None:
        return []
    notes = list(db["notes"].find(
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
    db = _get_db()
    if db is None:
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

        result = db["notes"].update_one(
            {"note_id": note_id, "user_id": str(user_id)},
            {"$set": update}
        )
        return {"updated": result.modified_count > 0}
    except Exception as e:
        logger.error(f"Update note error: {e}")
        return None


def api_delete_note(user_id, note_id):
    """Delete a note."""
    db = _get_db()
    if db is None:
        return False
    try:
        result = db["notes"].delete_one(
            {"note_id": note_id, "user_id": str(user_id)}
        )
        return result.deleted_count > 0
    except Exception as e:
        logger.error(f"Delete note error: {e}")
        return False



# ══════════════════════════════════════════════
# DASHBOARD API FUNCTIONS
# ══════════════════════════════════════════════
def api_get_dashboard_budget(user_id):
    """Get budget dashboard data."""
    db = _get_db()
    if db is None:
        return {"budget": None}
    try:
        now = datetime.now(EAT_ZONE)
        month_str = now.strftime("%Y-%m")
        from datetime import timedelta

        # Monthly spending by category
        pipeline = [
            {"$match": {"user_id": str(user_id), "month_str": month_str}},
            {"$group": {"_id": "$category", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
        ]
        cat_results = list(db["budgets"].aggregate(pipeline))
        by_category = {r["_id"]: round(r["total"], 2) for r in cat_results}
        total_spent = round(sum(by_category.values()), 2)
        total_count = sum(r["count"] for r in cat_results)

        # Budget limit
        limit_doc = db["budget_limits"].find_one({"user_id": str(user_id)})
        budget_limit = limit_doc.get("monthly_limit", 0) if limit_doc else 0

        # Income
        income_pipeline = [
            {"$match": {"user_id": str(user_id), "month_str": month_str}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
        ]
        income_result = list(db["income"].aggregate(income_pipeline))
        total_income = round(income_result[0]["total"], 2) if income_result else 0

        # Match get_effective_budget() logic: limit + income, or whichever exists
        if total_income > 0 and budget_limit > 0:
            effective_budget = budget_limit + total_income
        elif total_income > 0:
            effective_budget = total_income
        elif budget_limit > 0:
            effective_budget = budget_limit
        else:
            effective_budget = 0

        # Daily spending trend (14 days)
        cutoff = now - timedelta(days=14)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        daily_pipeline = [
            {"$match": {"user_id": str(user_id), "date": {"$gte": cutoff}}},
            {"$group": {"_id": "$date_str", "total": {"$sum": "$amount"}}},
            {"$sort": {"_id": 1}},
        ]
        daily_spending = [{"date": d["_id"], "total": round(d["total"], 2)}
                          for d in db["budgets"].aggregate(daily_pipeline) if d["_id"]]

        # Fallback: if date filter returned nothing, try date_str string comparison
        if not daily_spending:
            daily_pipeline_str = [
                {"$match": {"user_id": str(user_id), "date_str": {"$gte": cutoff_str}}},
                {"$group": {"_id": "$date_str", "total": {"$sum": "$amount"}}},
                {"$sort": {"_id": 1}},
            ]
            daily_spending = [{"date": d["_id"], "total": round(d["total"], 2)}
                              for d in db["budgets"].aggregate(daily_pipeline_str) if d["_id"]]

        return {"budget": {
            "total_spent": total_spent, "transaction_count": total_count,
            "budget_limit": budget_limit, "effective_budget": effective_budget,
            "total_income": total_income,
            "remaining": round(effective_budget - total_spent, 2) if effective_budget else 0,
            "pct_used": round((total_spent / effective_budget) * 100, 1) if effective_budget > 0 else 0,
            "by_category": by_category, "daily_spending": daily_spending,
            "month": month_str, "day_of_month": now.day,
        }}
    except Exception as e:
        logger.error(f"Dashboard budget error: {e}")
        return {"budget": None}


def api_get_dashboard_portfolio(user_id):
    """Get portfolio dashboard data with P/L info."""
    db = _get_db()
    if db is None:
        return {"portfolio": [], "total_value": 0}
    try:
        holdings = list(db["portfolios"].find(
            {"user_id": str(user_id), "shares": {"$gt": 0}},
            {"_id": 0}
        ))
        total_value = 0
        total_realized = 0
        for h in holdings:
            h["symbol"] = h.get("ticker", "?")
            h["quantity"] = h.get("shares", 0)
            h["avg_cost"] = h.get("avg_cost", h.get("buy_price", 0))
            h["buy_price"] = h["avg_cost"]
            h["total_cost_basis"] = round(h.get("shares", 0) * h["avg_cost"], 2)
            h["realized_pl"] = h.get("realized_pl", 0)
            h["value"] = h["total_cost_basis"]
            total_value += h["value"]
            total_realized += h["realized_pl"]
            if isinstance(h.get("added_at"), datetime):
                h["added_at"] = h["added_at"].isoformat()
            if isinstance(h.get("updated_at"), datetime):
                h["updated_at"] = h["updated_at"].isoformat()
        return {
            "portfolio": holdings,
            "total_value": round(total_value, 2),
            "total_realized_pl": round(total_realized, 2),
            "holding_count": len(holdings),
        }
    except Exception as e:
        logger.error(f"Dashboard portfolio error: {e}")
        return {"portfolio": [], "total_value": 0, "holding_count": 0}


def api_get_dashboard_goals(user_id):
    """Get goals dashboard data."""
    db = _get_db()
    if db is None:
        return {"goals": []}
    try:
        active = list(db["goals"].find(
            {"user_id": str(user_id), "status": "active"},
            {"_id": 0, "goal": 1, "progress": 1, "target": 1, "type": 1,
             "target_amount": 1, "saved_amount": 1, "created_at": 1}
        ))
        for g in active:
            if isinstance(g.get("created_at"), datetime):
                g["created_at"] = g["created_at"].isoformat()
            g.setdefault("progress", 0)
            g.setdefault("target", 100)
        completed_count = db["goals"].count_documents({"user_id": str(user_id), "status": "completed"})
        return {"goals": active, "active_count": len(active), "completed_count": completed_count}
    except Exception as e:
        logger.error(f"Dashboard goals error: {e}")
        return {"goals": [], "active_count": 0, "completed_count": 0}


def api_get_dashboard_budget_history(user_id, months=4):
    """Get month-over-month budget spending for trend comparison."""
    db = _get_db()
    if db is None:
        return {"months": []}
    try:
        now = datetime.now(EAT_ZONE)
        results = []
        for i in range(months):
            # Proper calendar month subtraction
            y = now.year
            m = now.month - i
            while m <= 0:
                m += 12
                y -= 1
            ms = f"{y}-{m:02d}"
            label = datetime(y, m, 1).strftime("%b")
            pipeline = [
                {"$match": {"user_id": str(user_id), "month_str": ms}},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
            ]
            agg = list(db["budgets"].aggregate(pipeline))
            total = round(agg[0]["total"], 2) if agg else 0
            count = agg[0]["count"] if agg else 0
            results.append({"month": ms, "label": label, "total_spent": total, "transactions": count})
        results.reverse()
        return {"months": results}
    except Exception as e:
        logger.error(f"Dashboard budget history error: {e}")
        return {"months": []}


def api_get_dashboard_wellness(user_id):
    """Get weekly wellness summary — mood, sleep, spending combined."""
    db = _get_db()
    if db is None:
        return {"wellness": None}
    try:
        now = datetime.now(EAT_ZONE)
        week_ago = now - timedelta(days=7)

        # Mood this week
        mood_entries = list(db["journal"].find(
            {"user_id": str(user_id), "date": {"$gte": week_ago}},
            {"mood_score": 1, "_id": 0}
        ))
        mood_scores = [e.get("mood_score", 3) for e in mood_entries]
        avg_mood = round(sum(mood_scores) / len(mood_scores), 1) if mood_scores else 0

        # Sleep this week
        sleep_entries = list(db["sleep"].find(
            {"user_id": str(user_id), "date": {"$gte": week_ago}},
            {"quality": 1, "hours": 1, "_id": 0}
        ))
        avg_sleep_hrs = round(sum(e.get("hours", 0) for e in sleep_entries) / len(sleep_entries), 1) if sleep_entries else 0
        avg_sleep_q = round(sum(e.get("quality", 3) for e in sleep_entries) / len(sleep_entries), 1) if sleep_entries else 0

        # Spending this week
        spend_pipeline = [
            {"$match": {"user_id": str(user_id), "date": {"$gte": week_ago}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
        ]
        spend_agg = list(db["budgets"].aggregate(spend_pipeline))
        week_spent = round(spend_agg[0]["total"], 2) if spend_agg else 0
        week_txns = spend_agg[0]["count"] if spend_agg else 0

        # Journal streak
        dates = sorted(set(
            e.get("date_str", "") for e in db["journal"].find(
                {"user_id": str(user_id), "date": {"$gte": now - timedelta(days=30)}},
                {"date_str": 1, "_id": 0}
            )
        ), reverse=True)
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        streak = 0
        if dates and (dates[0] == today or dates[0] == yesterday):
            streak = 1
            for i in range(1, len(dates)):
                prev = datetime.strptime(dates[i - 1], "%Y-%m-%d")
                curr = datetime.strptime(dates[i], "%Y-%m-%d")
                if (prev - curr).days == 1:
                    streak += 1
                else:
                    break

        # Gratitude count this week
        grat_count = db["gratitude"].count_documents(
            {"user_id": str(user_id), "date": {"$gte": week_ago}}
        )

        return {"wellness": {
            "avg_mood": avg_mood,
            "mood_entries": len(mood_scores),
            "avg_sleep_hours": avg_sleep_hrs,
            "avg_sleep_quality": avg_sleep_q,
            "sleep_entries": len(sleep_entries),
            "week_spent": week_spent,
            "week_transactions": week_txns,
            "journal_streak": streak,
            "gratitude_days": grat_count,
        }}
    except Exception as e:
        logger.error(f"Dashboard wellness error: {e}")
        return {"wellness": None}


# ══════════════════════════════════════════════
# HTTP API HANDLER
# ══════════════════════════════════════════════
class EmilyAPIHandler(BaseHTTPRequestHandler):
    """Handles both health checks and journal API requests."""

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', ALLOWED_ORIGIN)
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
        self.send_header('Access-Control-Allow-Origin', ALLOWED_ORIGIN)
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

        # Detailed status (no auth required — for monitoring)
        if path == "/status":
            db = _get_db()
            self._send_json({
                "status": "ok",
                "service": "emily-ai",
                "db_connected": db is not None,
                "started_at": bot_status.get("started_at"),
                "messages_processed": bot_status.get("messages_processed", 0),
                "commands_processed": bot_status.get("commands_processed", 0),
                "errors_count": bot_status.get("errors_count", 0),
                "tasks": bot_status.get("task_health", {}),
            })
            return

        # ── AUTH REQUIRED ROUTES ──
        user_id = self._get_user()
        if not user_id and path.startswith("/api/"):
            self._send_error("Unauthorized", 401)
            return

        # Get journal entries
        if path == "/api/journal/entries":
            days = _safe_int(params, "days", 14, max_val=365)
            limit = _safe_int(params, "limit", 20, max_val=200)
            entries = api_get_entries(user_id, days=days, limit=limit)
            self._send_json({"entries": entries})

        # Get mood trend
        elif path == "/api/journal/mood-trend":
            days = _safe_int(params, "days", 14, max_val=365)
            trend = api_get_mood_trend(user_id, days=days)
            self._send_json({"trend": trend})

        # Get stats
        elif path == "/api/journal/stats":
            days = _safe_int(params, "days", 30, max_val=365)
            stats = api_get_stats(user_id, days=days)
            self._send_json({"stats": stats or {}})

        # Get notes
        elif path == "/api/notes":
            notes = api_get_notes(user_id)
            self._send_json({"notes": notes})

        # Get gratitude
        elif path == "/api/journal/gratitude":
            doc = api_get_gratitude(user_id)
            self._send_json({"gratitude": doc})

        # Get sleep
        elif path == "/api/journal/sleep":
            days = _safe_int(params, "days", 7, max_val=365)
            entries = api_get_sleep(user_id, days=days)
            self._send_json({"sleep": entries})

        # ── DASHBOARD ENDPOINTS ──
        elif path == "/api/dashboard/budget":
            data = api_get_dashboard_budget(user_id)
            self._send_json(data)

        elif path == "/api/dashboard/portfolio":
            data = api_get_dashboard_portfolio(user_id)
            self._send_json(data)

        elif path == "/api/dashboard/goals":
            data = api_get_dashboard_goals(user_id)
            self._send_json(data)

        elif path == "/api/dashboard/budget-history":
            months = _safe_int(params, "months", 4, max_val=24)
            data = api_get_dashboard_budget_history(user_id, months=months)
            self._send_json(data)

        elif path == "/api/dashboard/wellness":
            data = api_get_dashboard_wellness(user_id)
            self._send_json(data)

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
            tags = body.get("tags", [])
            photos = body.get("photos", [])
            if not text and mood:
                entry = api_quick_mood(user_id, int(mood))
            elif text or photos:
                entry = api_add_entry(user_id, text or "📷 Photo entry", int(mood) if mood else None, tags=tags, photos=photos)
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

        # Update journal entry
        elif path == "/api/journal/entry/update":
            entry_id = body.get("id")
            if not entry_id:
                self._send_error("Entry id required")
                return
            result = api_update_entry(
                user_id, entry_id,
                text=body.get("text"),
                mood_score=body.get("mood_score"),
                tags=body.get("tags"),
                pinned=body.get("pinned"),
            )
            if result is not None:
                self._send_json({"updated": True})
            else:
                self._send_error("Failed to update", 500)

        # Delete journal entry
        elif path == "/api/journal/entry/delete":
            entry_id = body.get("id")
            if not entry_id:
                self._send_error("Entry id required")
                return
            if api_delete_entry(user_id, entry_id):
                self._send_json({"deleted": True})
            else:
                self._send_error("Failed to delete", 500)

        # Save gratitude
        elif path == "/api/journal/gratitude":
            items = body.get("items", [])
            if not items:
                self._send_error("items required")
                return
            doc = api_save_gratitude(user_id, items)
            if doc:
                self._send_json({"gratitude": doc}, 201)
            else:
                self._send_error("Failed to save", 500)

        # Save sleep
        elif path == "/api/journal/sleep":
            quality = body.get("quality")
            hours = body.get("hours", 7)
            if not quality:
                self._send_error("quality required")
                return
            doc = api_save_sleep(user_id, int(quality), float(hours))
            if doc:
                self._send_json({"sleep": doc}, 201)
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

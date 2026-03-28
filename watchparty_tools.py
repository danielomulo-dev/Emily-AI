import os
import logging
import certifi
import random
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import PyMongoError
from datetime import datetime
from dotenv import load_dotenv
import pytz

load_dotenv()
logger = logging.getLogger(__name__)

EAT_ZONE = pytz.timezone('Africa/Nairobi')

# --- CONNECT TO MONGODB ---
db = None
watchlist_col = None
ratings_col = None
watchparty_col = None

try:
    mongo_client = MongoClient(
        os.getenv("MONGO_URI"),
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=5000,
    )
    mongo_client.admin.command('ping')
    db = mongo_client["emily_brain_db"]
    watchlist_col = db["watchlists"]
    ratings_col = db["movie_ratings"]
    watchparty_col = db["watchparties"]

    watchlist_col.create_index([("guild_id", ASCENDING)])
    ratings_col.create_index([("guild_id", ASCENDING), ("title", ASCENDING)])
    watchparty_col.create_index([("guild_id", ASCENDING), ("status", ASCENDING)])

    logger.info("Watch party tools connected to MongoDB!")
except Exception as e:
    logger.error(f"Watch party MongoDB error: {e}")


def _now():
    return datetime.now(EAT_ZONE)


# ══════════════════════════════════════════════
# GROUP WATCHLIST
# ══════════════════════════════════════════════
def add_to_watchlist(guild_id, title, added_by, genre="", note=""):
    """Add a movie/show to the group watchlist."""
    if watchlist_col is None:
        return False
    try:
        # Check if already on watchlist
        existing = watchlist_col.find_one({
            "guild_id": str(guild_id),
            "title_lower": title.lower().strip(),
            "status": "pending",
        })
        if existing:
            return "duplicate"

        watchlist_col.insert_one({
            "guild_id": str(guild_id),
            "title": title.strip(),
            "title_lower": title.lower().strip(),
            "added_by": str(added_by),
            "genre": genre,
            "note": note,
            "votes": [],
            "vote_count": 0,
            "status": "pending",  # pending, watched, removed
            "added_at": _now(),
        })
        return True
    except PyMongoError as e:
        logger.error(f"Watchlist add error: {e}")
        return False


def remove_from_watchlist(guild_id, title):
    """Remove a movie from the group watchlist."""
    if watchlist_col is None:
        return False
    try:
        result = watchlist_col.update_one(
            {"guild_id": str(guild_id), "title_lower": title.lower().strip(), "status": "pending"},
            {"$set": {"status": "removed", "removed_at": _now()}}
        )
        return result.modified_count > 0
    except PyMongoError as e:
        logger.error(f"Watchlist remove error: {e}")
        return False


def get_watchlist(guild_id):
    """Get all pending movies on the watchlist."""
    if watchlist_col is None:
        return []
    try:
        return list(watchlist_col.find(
            {"guild_id": str(guild_id), "status": "pending"},
        ).sort("vote_count", DESCENDING))
    except PyMongoError as e:
        logger.error(f"Watchlist fetch error: {e}")
        return []


def vote_for_movie(guild_id, title, user_id):
    """Vote for a movie on the watchlist. Each user gets one vote per movie."""
    if watchlist_col is None:
        return False, "Database not connected"
    try:
        movie = watchlist_col.find_one({
            "guild_id": str(guild_id),
            "title_lower": title.lower().strip(),
            "status": "pending",
        })
        if not movie:
            return False, "Movie not found on watchlist"

        if str(user_id) in movie.get("votes", []):
            return False, "You already voted for this one"

        watchlist_col.update_one(
            {"_id": movie["_id"]},
            {
                "$push": {"votes": str(user_id)},
                "$inc": {"vote_count": 1},
            }
        )
        return True, movie["vote_count"] + 1
    except PyMongoError as e:
        logger.error(f"Vote error: {e}")
        return False, "Vote failed"


def mark_as_watched(guild_id, title):
    """Mark a movie as watched (moves from pending to watched)."""
    if watchlist_col is None:
        return False
    try:
        result = watchlist_col.update_one(
            {"guild_id": str(guild_id), "title_lower": title.lower().strip(), "status": "pending"},
            {"$set": {"status": "watched", "watched_at": _now()}}
        )
        return result.modified_count > 0
    except PyMongoError as e:
        logger.error(f"Mark watched error: {e}")
        return False


def get_watch_history(guild_id, limit=20):
    """Get recently watched movies."""
    if watchlist_col is None:
        return []
    try:
        return list(watchlist_col.find(
            {"guild_id": str(guild_id), "status": "watched"},
        ).sort("watched_at", DESCENDING).limit(limit))
    except PyMongoError as e:
        logger.error(f"Watch history error: {e}")
        return []


def get_random_pick(guild_id):
    """Randomly pick a movie from the watchlist (for indecisive groups)."""
    movies = get_watchlist(guild_id)
    if not movies:
        return None
    return random.choice(movies)


def get_top_voted(guild_id, limit=5):
    """Get the top voted movies."""
    if watchlist_col is None:
        return []
    try:
        return list(watchlist_col.find(
            {"guild_id": str(guild_id), "status": "pending", "vote_count": {"$gt": 0}},
        ).sort("vote_count", DESCENDING).limit(limit))
    except PyMongoError as e:
        logger.error(f"Top voted error: {e}")
        return []


# ══════════════════════════════════════════════
# MOVIE RATINGS
# ══════════════════════════════════════════════
def rate_movie(guild_id, title, user_id, score, review=""):
    """Rate a movie (1-10). Each user can rate once per movie."""
    if ratings_col is None:
        return False
    try:
        if not (1 <= score <= 10):
            return "invalid_score"

        # Check if already rated
        existing = ratings_col.find_one({
            "guild_id": str(guild_id),
            "title_lower": title.lower().strip(),
            "user_id": str(user_id),
        })
        if existing:
            # Update existing rating
            ratings_col.update_one(
                {"_id": existing["_id"]},
                {"$set": {"score": score, "review": review, "updated_at": _now()}}
            )
            return "updated"

        ratings_col.insert_one({
            "guild_id": str(guild_id),
            "title": title.strip(),
            "title_lower": title.lower().strip(),
            "user_id": str(user_id),
            "score": score,
            "review": review,
            "rated_at": _now(),
        })
        return True
    except PyMongoError as e:
        logger.error(f"Rating error: {e}")
        return False


def get_movie_ratings(guild_id, title):
    """Get all ratings for a specific movie."""
    if ratings_col is None:
        return []
    try:
        return list(ratings_col.find({
            "guild_id": str(guild_id),
            "title_lower": title.lower().strip(),
        }))
    except PyMongoError as e:
        logger.error(f"Ratings fetch error: {e}")
        return []


def get_group_top_rated(guild_id, limit=10):
    """Get highest-rated movies by the group (average score)."""
    if ratings_col is None:
        return []
    try:
        pipeline = [
            {"$match": {"guild_id": str(guild_id)}},
            {"$group": {
                "_id": "$title_lower",
                "title": {"$first": "$title"},
                "avg_score": {"$avg": "$score"},
                "num_ratings": {"$sum": 1},
                "scores": {"$push": "$score"},
            }},
            {"$match": {"num_ratings": {"$gte": 1}}},
            {"$sort": {"avg_score": -1}},
            {"$limit": limit},
        ]
        return list(ratings_col.aggregate(pipeline))
    except PyMongoError as e:
        logger.error(f"Top rated error: {e}")
        return []


# ══════════════════════════════════════════════
# WATCH PARTY SCHEDULING
# ══════════════════════════════════════════════
def schedule_watchparty(guild_id, channel_id, title, watch_time, host_id):
    """Schedule a watch party."""
    if watchparty_col is None:
        return False
    try:
        watchparty_col.insert_one({
            "guild_id": str(guild_id),
            "channel_id": str(channel_id),
            "title": title.strip(),
            "watch_time": watch_time,
            "host_id": str(host_id),
            "attendees": [str(host_id)],
            "status": "scheduled",  # scheduled, live, ended
            "created_at": _now(),
        })
        return True
    except PyMongoError as e:
        logger.error(f"Schedule error: {e}")
        return False


def join_watchparty(guild_id, user_id):
    """Join the next scheduled watch party."""
    if watchparty_col is None:
        return False, "Database not connected"
    try:
        party = watchparty_col.find_one({
            "guild_id": str(guild_id),
            "status": "scheduled",
        }, sort=[("watch_time", ASCENDING)])

        if not party:
            return False, "No watch party scheduled"

        if str(user_id) in party.get("attendees", []):
            return False, "You're already in!"

        watchparty_col.update_one(
            {"_id": party["_id"]},
            {"$push": {"attendees": str(user_id)}}
        )
        return True, party["title"]
    except PyMongoError as e:
        logger.error(f"Join error: {e}")
        return False, "Failed to join"


def get_next_watchparty(guild_id):
    """Get the next scheduled watch party."""
    if watchparty_col is None:
        return None
    try:
        return watchparty_col.find_one(
            {"guild_id": str(guild_id), "status": "scheduled"},
            sort=[("watch_time", ASCENDING)]
        )
    except PyMongoError as e:
        logger.error(f"Get party error: {e}")
        return None


def get_due_watchparties():
    """Get watch parties that should start now (for reminders)."""
    if watchparty_col is None:
        return []
    try:
        now = _now()
        return list(watchparty_col.find({
            "watch_time": {"$lte": now},
            "status": "scheduled",
        }))
    except PyMongoError as e:
        logger.error(f"Due parties error: {e}")
        return []


def start_watchparty(party_id):
    """Mark a watch party as live."""
    if watchparty_col is None:
        return
    try:
        watchparty_col.update_one(
            {"_id": party_id},
            {"$set": {"status": "live", "started_at": _now()}}
        )
    except PyMongoError as e:
        logger.error(f"Start party error: {e}")


def end_watchparty(guild_id):
    """End the current live watch party."""
    if watchparty_col is None:
        return None
    try:
        party = watchparty_col.find_one_and_update(
            {"guild_id": str(guild_id), "status": "live"},
            {"$set": {"status": "ended", "ended_at": _now()}},
        )
        if party:
            # Also mark the movie as watched on the watchlist
            mark_as_watched(guild_id, party["title"])
        return party
    except PyMongoError as e:
        logger.error(f"End party error: {e}")
        return None


# ══════════════════════════════════════════════
# FORMATTED OUTPUTS
# ══════════════════════════════════════════════
def format_watchlist(guild_id):
    """Format the watchlist for display."""
    movies = get_watchlist(guild_id)
    if not movies:
        return "📋 Watchlist is empty! Add movies with `!addmovie`"

    lines = ["🎬 **Group Watchlist:**\n"]
    for i, m in enumerate(movies, 1):
        votes = m.get("vote_count", 0)
        vote_str = f" — 🗳️ {votes} vote{'s' if votes != 1 else ''}" if votes > 0 else ""
        genre = f" ({m['genre']})" if m.get("genre") else ""
        note = f" — *{m['note']}*" if m.get("note") else ""
        lines.append(f"**{i}.** {m['title']}{genre}{vote_str}{note}")

    lines.append(f"\n_{len(movies)} movies waiting_ | Vote with `!vote <title>` | Random pick: `!pick`")
    return "\n".join(lines)


def format_ratings(guild_id, title):
    """Format ratings for a specific movie."""
    ratings = get_movie_ratings(guild_id, title)
    if not ratings:
        return f"No ratings yet for **{title}**. Rate it with `!rate {title} 8`"

    total = sum(r["score"] for r in ratings)
    avg = total / len(ratings)

    lines = [f"⭐ **Ratings for: {title}**\n"]
    lines.append(f"**Average: {avg:.1f}/10** ({len(ratings)} ratings)\n")

    for r in ratings:
        stars = "⭐" * min(int(r["score"]), 10)
        review = f' — *"{r["review"]}"*' if r.get("review") else ""
        lines.append(f"<@{r['user_id']}>: **{r['score']}/10** {stars}{review}")

    return "\n".join(lines)


def format_top_rated(guild_id):
    """Format top rated movies."""
    top = get_group_top_rated(guild_id)
    if not top:
        return "No ratings yet! Watch something and rate it with `!rate`"

    lines = ["🏆 **Group's Top Rated Movies:**\n"]
    for i, m in enumerate(top, 1):
        medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"**{i}.**"
        lines.append(f"{medal} **{m['title']}** — {m['avg_score']:.1f}/10 ({m['num_ratings']} ratings)")

    return "\n".join(lines)


def format_watch_history(guild_id):
    """Format watch history."""
    history = get_watch_history(guild_id)
    if not history:
        return "No movies watched yet as a group!"

    lines = ["📼 **Watch History:**\n"]
    for m in history:
        date = m.get("watched_at", m.get("added_at", _now())).strftime("%b %d")
        lines.append(f"• **{m['title']}** — watched on {date}")

    lines.append(f"\n_{len(history)} movies watched_ | Rate with `!rate <title> <score>`")
    return "\n".join(lines)


def format_watchparty(party):
    """Format a scheduled watch party."""
    if not party:
        return "No watch party scheduled! Use `!watchparty <title> <time>`"

    time_str = party["watch_time"].strftime("%A, %b %d at %I:%M %p EAT")
    attendees = len(party.get("attendees", []))
    mentions = " ".join([f"<@{uid}>" for uid in party.get("attendees", [])])

    return (
        f"🍿 **Watch Party Scheduled!**\n\n"
        f"**Movie:** {party['title']}\n"
        f"**When:** {time_str}\n"
        f"**Attending ({attendees}):** {mentions}\n\n"
        f"Join with `!join` | Emily will ping everyone when it's time!"
    )


# ══════════════════════════════════════════════
# MOVIE SUGGESTION TRACKING
# ══════════════════════════════════════════════
suggestions_col = None
movie_settings_col = None

try:
    if db is not None:
        suggestions_col = db["movie_suggestions"]
        movie_settings_col = db["movie_settings"]
        suggestions_col.create_index([("guild_id", ASCENDING), ("suggested_at", DESCENDING)])
        movie_settings_col.create_index([("guild_id", ASCENDING)])
except Exception as e:
    logger.error(f"Movie suggestion DB error: {e}")


def set_movie_channel(guild_id, channel_id, suggest_time="19:00"):
    """Set the channel and time for movie suggestions."""
    if movie_settings_col is None:
        return False
    try:
        movie_settings_col.update_one(
            {"guild_id": str(guild_id)},
            {"$set": {
                "channel_id": str(channel_id),
                "suggest_time": suggest_time,
                "enabled": True,
                "updated_at": _now(),
            }},
            upsert=True,
        )
        return True
    except PyMongoError as e:
        logger.error(f"Set movie channel error: {e}")
        return False


def get_movie_suggestion_servers():
    """Get all servers with movie suggestions enabled."""
    if movie_settings_col is None:
        return []
    try:
        return list(movie_settings_col.find({"enabled": True}))
    except PyMongoError as e:
        logger.error(f"Movie servers error: {e}")
        return []


def log_movie_suggestion(guild_id, title, language, year, imdb_rating, rt_rating, genre, plot):
    """Log a movie suggestion to avoid repeats."""
    if suggestions_col is None:
        return
    try:
        suggestions_col.insert_one({
            "guild_id": str(guild_id),
            "title": title,
            "title_lower": title.lower().strip(),
            "language": language,
            "year": year,
            "imdb_rating": imdb_rating,
            "rt_rating": rt_rating,
            "genre": genre,
            "plot": plot,
            "suggested_at": _now(),
        })
    except PyMongoError as e:
        logger.error(f"Log suggestion error: {e}")


def get_past_suggestions(guild_id, limit=50):
    """Get previously suggested titles to avoid repeats."""
    if suggestions_col is None:
        return []
    try:
        docs = suggestions_col.find(
            {"guild_id": str(guild_id)},
            {"title_lower": 1}
        ).sort("suggested_at", DESCENDING).limit(limit)
        return [d["title_lower"] for d in docs]
    except PyMongoError as e:
        logger.error(f"Past suggestions error: {e}")
        return []


# Language/genre pools for variety
MOVIE_LANGUAGES = [
    # Weighted: English appears more since it has the largest accessible catalog
    "English", "English", "English", "English",
    "Korean (K-drama/Korean cinema)", "Korean (K-drama/Korean cinema)",
    "Japanese", "Japanese",
    "French",
    "Spanish",
    "Italian",
    "German",
    "Hindi (Bollywood)",
    "Swahili / East African",
    "Nigerian (Nollywood)",
    "Scandinavian (Danish/Swedish/Norwegian)",
    "Thai",
    "Brazilian Portuguese",
]

MOVIE_GENRES = [
    "thriller", "drama", "comedy", "sci-fi", "horror", "romance",
    "action", "mystery", "documentary", "animation", "crime",
    "war", "historical", "indie", "psychological thriller",
    "dark comedy", "heist", "survival", "coming-of-age", "fantasy",
]

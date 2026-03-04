import os
import logging
import certifi
from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure, PyMongoError
from dotenv import load_dotenv
from datetime import datetime
import pytz

load_dotenv()
logger = logging.getLogger(__name__)

# --- CONSTANTS ---
EAT_ZONE = pytz.timezone('Africa/Nairobi')
MAX_FACTS = 50       # Max stored facts per user
MAX_HISTORY = 30     # Max chat messages per user
FACT_SIMILARITY_THRESHOLD = 0.85  # For dedup (simple keyword overlap)

# --- CONNECT TO MONGODB ---
db = None
users_col = None
reminders_col = None

try:
    mongo_client = MongoClient(
        os.getenv("MONGO_URI"),
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=5000,  # Fail fast if can't connect
        connectTimeoutMS=5000,
    )
    # Test the connection
    mongo_client.admin.command('ping')

    db = mongo_client["emily_brain_db"]
    users_col = db["users"]
    reminders_col = db["reminders"]

    # --- CREATE INDEXES (runs once, no-ops if already exist) ---
    reminders_col.create_index([("time", ASCENDING), ("status", ASCENDING)])
    reminders_col.create_index([("user_id", ASCENDING)])
    users_col.create_index([("_id", ASCENDING)])  # Already default, but explicit

    logger.info("Successfully connected to MongoDB!")

except ConnectionFailure as e:
    logger.error(f"Could not connect to MongoDB: {e}")
except Exception as e:
    logger.error(f"MongoDB initialization error: {e}")


def _check_db():
    """Ensure DB is connected before operations."""
    if users_col is None:
        raise ConnectionError("MongoDB is not connected. Check MONGO_URI.")


# ══════════════════════════════════════════════
# FACT DEDUPLICATION
# ══════════════════════════════════════════════
def _fact_is_duplicate(existing_facts, new_fact_text):
    """
    Check if a fact is too similar to existing ones.
    Uses simple keyword overlap — not perfect but catches exact and near dupes.
    """
    new_words = set(new_fact_text.lower().split())
    if not new_words:
        return True  # Empty fact

    for existing in existing_facts:
        existing_text = existing.get("fact", "") if isinstance(existing, dict) else str(existing)
        existing_words = set(existing_text.lower().split())

        if not existing_words:
            continue

        # Calculate overlap
        overlap = len(new_words & existing_words)
        max_len = max(len(new_words), len(existing_words))
        similarity = overlap / max_len if max_len > 0 else 0

        if similarity >= FACT_SIMILARITY_THRESHOLD:
            return True

        # Exact substring match
        if new_fact_text.lower().strip() in existing_text.lower() or \
           existing_text.lower().strip() in new_fact_text.lower():
            return True

    return False


# ══════════════════════════════════════════════
# USER PROFILE FUNCTIONS
# ══════════════════════════════════════════════
def get_user_profile(user_id):
    """Fetches user profile and flattens facts into strings for the AI prompt."""
    _check_db()
    user_id = str(user_id)

    try:
        user_data = users_col.find_one({"_id": user_id})
    except PyMongoError as e:
        logger.error(f"DB error fetching profile for {user_id}: {e}")
        return {"_id": user_id, "facts": [], "style": "friendly", "history": []}

    if not user_data:
        return {"_id": user_id, "facts": [], "style": "friendly", "history": []}

    # Flatten structured facts for Emily's prompt
    raw_facts = user_data.get("facts", [])
    clean_facts = []
    for f in raw_facts:
        if isinstance(f, dict):
            fact_text = f.get("fact", "")
            if fact_text:
                clean_facts.append(fact_text)
        elif f:
            clean_facts.append(str(f))

    user_data["facts"] = clean_facts
    return user_data


def update_user_fact(user_id, fact_text, category="general"):
    """
    Adds a structured fact with deduplication and cap.
    - Skips if too similar to an existing fact
    - Removes oldest fact if at MAX_FACTS limit
    """
    _check_db()
    user_id = str(user_id)

    if not fact_text or not fact_text.strip():
        return

    try:
        # Get existing facts to check for duplicates
        user_data = users_col.find_one({"_id": user_id}, {"facts": 1})
        existing_facts = user_data.get("facts", []) if user_data else []

        # Skip duplicates
        if _fact_is_duplicate(existing_facts, fact_text):
            logger.info(f"Skipping duplicate fact for {user_id}: {fact_text[:50]}...")
            return

        fact_entry = {
            "fact": fact_text,
            "category": category,
            "added_at": datetime.now(EAT_ZONE),
        }

        # If at cap, remove the oldest fact first
        if len(existing_facts) >= MAX_FACTS:
            users_col.update_one(
                {"_id": user_id},
                {"$pop": {"facts": -1}}  # Remove first (oldest) element
            )
            logger.info(f"Fact cap reached for {user_id}, removed oldest fact")

        # Add the new fact
        users_col.update_one(
            {"_id": user_id},
            {"$push": {"facts": fact_entry}},
            upsert=True,
        )
        logger.info(f"Fact saved for {user_id}: {fact_text[:60]}")

    except PyMongoError as e:
        logger.error(f"DB error saving fact for {user_id}: {e}")


def set_voice_mode(user_id, enabled):
    """Turns voice mode ON (True) or OFF (False) for a user."""
    _check_db()
    user_id = str(user_id)

    try:
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"voice_mode": enabled}},
            upsert=True,
        )
    except PyMongoError as e:
        logger.error(f"DB error setting voice mode for {user_id}: {e}")


# ══════════════════════════════════════════════
# CHAT HISTORY FUNCTIONS
# ══════════════════════════════════════════════
def add_message_to_history(user_id, role, message_parts):
    """Saves a message to conversation history (capped at MAX_HISTORY)."""
    _check_db()
    user_id = str(user_id)

    new_message = {
        "role": role,
        "parts": message_parts,
        "timestamp": datetime.now(EAT_ZONE),
    }

    try:
        users_col.update_one(
            {"_id": user_id},
            {"$push": {"history": {"$each": [new_message], "$slice": -MAX_HISTORY}}},
            upsert=True,
        )
    except PyMongoError as e:
        logger.error(f"DB error saving history for {user_id}: {e}")


def get_chat_history(user_id):
    """Retrieves recent chat messages. Returns a new list (safe to modify)."""
    _check_db()
    user_id = str(user_id)

    try:
        data = users_col.find_one({"_id": user_id}, {"history": 1})
        if data and "history" in data:
            # Return a fresh copy so callers can append without side effects
            return [{"role": msg["role"], "parts": msg["parts"]} for msg in data["history"]]
    except PyMongoError as e:
        logger.error(f"DB error fetching history for {user_id}: {e}")

    return []


def clear_chat_history(user_id):
    """Clears all chat history for a user (useful for !reset command)."""
    _check_db()
    user_id = str(user_id)

    try:
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"history": []}}
        )
        logger.info(f"History cleared for {user_id}")
    except PyMongoError as e:
        logger.error(f"DB error clearing history for {user_id}: {e}")


def clear_user_facts(user_id):
    """Clears all stored facts for a user (useful for !forget command)."""
    _check_db()
    user_id = str(user_id)

    try:
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"facts": []}}
        )
        logger.info(f"Facts cleared for {user_id}")
    except PyMongoError as e:
        logger.error(f"DB error clearing facts for {user_id}: {e}")


# ══════════════════════════════════════════════
# REMINDER FUNCTIONS
# ══════════════════════════════════════════════
def add_reminder(user_id, channel_id, remind_time, reminder_text):
    """Saves a scheduled reminder."""
    _check_db()

    try:
        reminders_col.insert_one({
            "user_id": str(user_id),
            "channel_id": str(channel_id),
            "time": remind_time,
            "text": reminder_text,
            "status": "pending",
            "created_at": datetime.now(EAT_ZONE),
        })
    except PyMongoError as e:
        logger.error(f"DB error adding reminder: {e}")


def get_due_reminders():
    """Finds reminders that are due and still pending."""
    if reminders_col is None:
        return []

    try:
        now_eat = datetime.now(EAT_ZONE)
        return list(reminders_col.find({
            "time": {"$lte": now_eat},
            "status": "pending",
        }))
    except PyMongoError as e:
        logger.error(f"DB error fetching reminders: {e}")
        return []


def mark_reminder_sent(reminder_id):
    """Marks a reminder as sent (safer than deleting — keeps audit trail)."""
    if reminders_col is None:
        return

    try:
        reminders_col.update_one(
            {"_id": reminder_id},
            {"$set": {
                "status": "sent",
                "sent_at": datetime.now(EAT_ZONE),
            }}
        )
    except PyMongoError as e:
        logger.error(f"DB error marking reminder sent: {e}")


def delete_reminder(reminder_id):
    """Hard-deletes a reminder."""
    if reminders_col is None:
        return

    try:
        reminders_col.delete_one({"_id": reminder_id})
    except PyMongoError as e:
        logger.error(f"DB error deleting reminder: {e}")

import os
import random
import logging
import certifi
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import PyMongoError
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pytz

load_dotenv()
logger = logging.getLogger(__name__)

EAT_ZONE = pytz.timezone('Africa/Nairobi')

# --- CONNECT TO MONGODB ---
db = None
goals_col = None
anniversaries_col = None

try:
    mongo_client = MongoClient(
        os.getenv("MONGO_URI"),
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=5000,
    )
    mongo_client.admin.command('ping')
    db = mongo_client["emily_brain_db"]
    goals_col = db["goals"]
    anniversaries_col = db["anniversaries"]

    goals_col.create_index([("user_id", ASCENDING), ("status", ASCENDING)])
    anniversaries_col.create_index([("guild_id", ASCENDING), ("month_day", ASCENDING)])

    logger.info("Social tools connected to MongoDB!")
except Exception as e:
    logger.error(f"Social tools MongoDB error: {e}")


def _now():
    return datetime.now(EAT_ZONE)


# ══════════════════════════════════════════════
# GOAL TRACKER
# ══════════════════════════════════════════════
def add_goal(user_id, goal_text, category="personal", deadline=None, target_amount=None):
    """Add a goal for a user. target_amount enables amount-based tracking."""
    if goals_col is None:
        return False
    try:
        goals_col.insert_one({
            "user_id": str(user_id),
            "goal": goal_text,
            "category": category,
            "deadline": deadline,
            "status": "active",
            "progress": 0,
            "target_amount": float(target_amount) if target_amount else None,
            "saved_amount": 0,
            "check_ins": [],
            "created_at": _now(),
        })
        return True
    except PyMongoError as e:
        logger.error(f"Goal add error: {e}")
        return False


def get_active_goals(user_id):
    """Get all active goals for a user."""
    if goals_col is None:
        return []
    try:
        return list(goals_col.find({
            "user_id": str(user_id),
            "status": "active",
        }).sort("created_at", DESCENDING))
    except PyMongoError as e:
        logger.error(f"Goal fetch error: {e}")
        return []


def update_goal_progress(user_id, goal_index, progress, note=""):
    """Update progress on a goal (0-100)."""
    if goals_col is None:
        return False
    try:
        goals = get_active_goals(user_id)
        if goal_index < 0 or goal_index >= len(goals):
            return False

        goal = goals[goal_index]
        check_in = {
            "progress": min(100, max(0, progress)),
            "note": note,
            "date": _now(),
        }

        goals_col.update_one(
            {"_id": goal["_id"]},
            {
                "$set": {"progress": check_in["progress"]},
                "$push": {"check_ins": check_in},
            }
        )

        # Auto-complete if 100%
        if progress >= 100:
            goals_col.update_one(
                {"_id": goal["_id"]},
                {"$set": {"status": "completed", "completed_at": _now()}}
            )

        return True
    except PyMongoError as e:
        logger.error(f"Goal update error: {e}")
        return False


def update_saved_amount(user_id, goal_index, amount, mode="set"):
    """
    Update saved amount on a goal and auto-calculate percentage.
    mode: 'set' = set total saved, 'add' = add to current saved
    Returns: (success, result_dict) or (False, error_msg)
    """
    if goals_col is None:
        return False, "Database not connected"
    try:
        goals = get_active_goals(user_id)
        if goal_index < 0 or goal_index >= len(goals):
            return False, "Invalid goal number"

        goal = goals[goal_index]
        target = goal.get("target_amount")
        if not target:
            return False, "This goal doesn't have a target amount. Use `!progress` for percentage-based goals."

        current_saved = goal.get("saved_amount", 0)
        if mode == "add":
            new_saved = current_saved + float(amount)
        else:
            new_saved = float(amount)

        new_saved = max(0, new_saved)
        progress = min(100, int((new_saved / target) * 100))

        check_in = {
            "progress": progress,
            "saved_amount": new_saved,
            "note": f"Saved KES {new_saved:,.2f} / KES {target:,.2f}",
            "date": _now(),
        }

        update_fields = {
            "progress": progress,
            "saved_amount": new_saved,
        }

        # Auto-complete if target reached
        if new_saved >= target:
            update_fields["status"] = "completed"
            update_fields["completed_at"] = _now()

        goals_col.update_one(
            {"_id": goal["_id"]},
            {
                "$set": update_fields,
                "$push": {"check_ins": check_in},
            }
        )

        return True, {
            "goal": goal["goal"],
            "saved": new_saved,
            "target": target,
            "progress": progress,
            "remaining": max(0, target - new_saved),
            "completed": new_saved >= target,
        }
    except PyMongoError as e:
        logger.error(f"Saved amount update error: {e}")
        return False, f"Error: {e}"


def complete_goal(user_id, goal_index):
    """Mark a goal as completed."""
    return update_goal_progress(user_id, goal_index, 100, "Goal completed!")


def remove_goal(user_id, goal_index):
    """Remove/abandon a goal."""
    if goals_col is None:
        return False
    try:
        goals = get_active_goals(user_id)
        if goal_index < 0 or goal_index >= len(goals):
            return False
        goals_col.update_one(
            {"_id": goals[goal_index]["_id"]},
            {"$set": {"status": "abandoned", "abandoned_at": _now()}}
        )
        return True
    except PyMongoError as e:
        logger.error(f"Goal remove error: {e}")
        return False


def get_completed_goals(user_id, limit=10):
    """Get recently completed goals."""
    if goals_col is None:
        return []
    try:
        return list(goals_col.find({
            "user_id": str(user_id),
            "status": "completed",
        }).sort("completed_at", DESCENDING).limit(limit))
    except PyMongoError as e:
        return []


def get_all_users_with_goals():
    """Get all users with active goals (for weekly check-ins)."""
    if goals_col is None:
        return []
    try:
        return goals_col.distinct("user_id", {"status": "active"})
    except PyMongoError as e:
        return []


def format_goals(user_id):
    """Format goals for display."""
    active = get_active_goals(user_id)
    completed = get_completed_goals(user_id, limit=5)

    if not active and not completed:
        return "No goals set! Start with:\n`!goal Save 3500 for water dispenser` (percentage tracking)\n`!savinggoal 3500 Water dispenser` (amount tracking)"

    lines = ["🎯 **Your Goals:**\n"]

    if active:
        lines.append("**Active:**")
        for i, g in enumerate(active):
            bar = _progress_bar(g["progress"])
            deadline = f" (due: {g['deadline'].strftime('%b %d')})" if g.get("deadline") else ""

            # Show amounts for financial goals
            target = g.get("target_amount")
            saved = g.get("saved_amount", 0)
            if target:
                amount_str = f" — KES {saved:,.0f} / {target:,.0f}"
                lines.append(f"**{i+1}.** {g['goal']} {bar} {g['progress']}%{amount_str}{deadline}")
            else:
                lines.append(f"**{i+1}.** {g['goal']} {bar} {g['progress']}%{deadline}")
        lines.append("")

    if completed:
        lines.append("**Completed:** ✅")
        for g in completed:
            date = g.get("completed_at", g["created_at"]).strftime("%b %d")
            target = g.get("target_amount")
            amount_str = f" (KES {target:,.0f})" if target else ""
            lines.append(f"✅ ~~{g['goal']}~~{amount_str} — {date}")

    lines.append(f"\n**Commands:**")
    lines.append(f"`!saved <#> <amount>` — Update amount saved")
    lines.append(f"`!addsaved <#> <amount>` — Add to current savings")
    lines.append(f"`!progress <#> <percent>` — Update percentage")
    lines.append(f"`!done <#>` — Complete a goal")
    return "\n".join(lines)


def _progress_bar(percent):
    """Generate a text progress bar."""
    filled = int(percent / 10)
    empty = 10 - filled
    return f"[{'█' * filled}{'░' * empty}]"


# ══════════════════════════════════════════════
# ACCOUNTABILITY CHECK
# ══════════════════════════════════════════════
def get_stale_goals(days=7):
    """Get goals that haven't been updated in X days."""
    if goals_col is None:
        return []
    try:
        cutoff = _now() - timedelta(days=days)
        stale = []

        active_goals = goals_col.find({"status": "active"})
        for goal in active_goals:
            last_activity = goal["created_at"]
            if goal.get("check_ins"):
                last_activity = goal["check_ins"][-1]["date"]
            if last_activity < cutoff:
                stale.append(goal)

        return stale
    except PyMongoError as e:
        logger.error(f"Stale goals error: {e}")
        return []


def generate_accountability_message(goal):
    """Generate a fun accountability nudge for a stale goal."""
    messages = [
        f"Oi! Your goal **\"{goal['goal']}\"** is at {goal['progress']}% and hasn't been updated in a while. What's the progress, manze?",
        f"Remember **\"{goal['goal']}\"**? It's sitting at {goal['progress']}%. Let's not let it collect dust. Update with `!progress`!",
        f"Checking in on **\"{goal['goal']}\"** ({goal['progress']}%). Still on it or should we talk strategy? 💪",
        f"Your goal **\"{goal['goal']}\"** misses you. It's been lonely at {goal['progress']}%. Show it some love! 📊",
        f"Manze, **\"{goal['goal']}\"** is waiting at {goal['progress']}%. Even 1% progress counts. What's the status?",
    ]
    return random.choice(messages)


# ══════════════════════════════════════════════
# ANNIVERSARY / BIRTHDAY TRACKER
# ══════════════════════════════════════════════
def add_anniversary(guild_id, user_id, name, date, event_type="birthday"):
    """Add a birthday or anniversary."""
    if anniversaries_col is None:
        return False
    try:
        month_day = date.strftime("%m-%d")

        anniversaries_col.update_one(
            {
                "guild_id": str(guild_id),
                "name_lower": name.lower().strip(),
                "event_type": event_type,
            },
            {"$set": {
                "guild_id": str(guild_id),
                "added_by": str(user_id),
                "name": name.strip(),
                "name_lower": name.lower().strip(),
                "date": date,
                "month_day": month_day,
                "event_type": event_type,
                "updated_at": _now(),
            }},
            upsert=True,
        )
        return True
    except PyMongoError as e:
        logger.error(f"Anniversary add error: {e}")
        return False


def remove_anniversary(guild_id, name, event_type="birthday"):
    """Remove an anniversary."""
    if anniversaries_col is None:
        return False
    try:
        result = anniversaries_col.delete_one({
            "guild_id": str(guild_id),
            "name_lower": name.lower().strip(),
            "event_type": event_type,
        })
        return result.deleted_count > 0
    except PyMongoError as e:
        logger.error(f"Anniversary remove error: {e}")
        return False


def get_todays_events(guild_id):
    """Get all events happening today."""
    if anniversaries_col is None:
        return []
    try:
        today = _now().strftime("%m-%d")
        return list(anniversaries_col.find({
            "guild_id": str(guild_id),
            "month_day": today,
        }))
    except PyMongoError as e:
        logger.error(f"Today events error: {e}")
        return []


def get_upcoming_events(guild_id, days=7):
    """Get events in the next X days."""
    if anniversaries_col is None:
        return []
    try:
        now = _now()
        upcoming_dates = [(now + timedelta(days=d)).strftime("%m-%d") for d in range(days + 1)]

        return list(anniversaries_col.find({
            "guild_id": str(guild_id),
            "month_day": {"$in": upcoming_dates},
        }))
    except PyMongoError as e:
        logger.error(f"Upcoming events error: {e}")
        return []


def get_all_events(guild_id):
    """Get all saved events for a guild."""
    if anniversaries_col is None:
        return []
    try:
        return list(anniversaries_col.find(
            {"guild_id": str(guild_id)},
        ).sort("month_day", ASCENDING))
    except PyMongoError as e:
        logger.error(f"All events error: {e}")
        return []


def get_guilds_with_events():
    """Get all guild IDs that have events saved."""
    if anniversaries_col is None:
        return []
    try:
        return anniversaries_col.distinct("guild_id")
    except PyMongoError as e:
        return []


def format_anniversaries(guild_id):
    """Format all saved events."""
    events = get_all_events(guild_id)
    if not events:
        return "No birthdays or anniversaries saved! Add with `!birthday <name> <date>`"

    lines = ["🎂 **Saved Events:**\n"]
    for e in events:
        icon = "🎂" if e["event_type"] == "birthday" else "💍"
        date_str = e["date"].strftime("%B %d")
        lines.append(f"{icon} **{e['name']}** — {date_str}")

    upcoming = get_upcoming_events(guild_id, days=14)
    if upcoming:
        lines.append("\n📅 **Coming up in the next 2 weeks:**")
        for e in upcoming:
            icon = "🎂" if e["event_type"] == "birthday" else "💍"
            date_str = e["date"].strftime("%B %d")
            lines.append(f"{icon} **{e['name']}** — {date_str}")

    return "\n".join(lines)


# ══════════════════════════════════════════════
# DAILY LEARNING TOPICS
# ══════════════════════════════════════════════
LEARNING_TOPICS = {
    "finance": [
        "What is Dollar-Cost Averaging (DCA) and why it's the lazy investor's best friend",
        "How to read a company's P/E ratio and what it actually tells you",
        "The difference between stocks, bonds, and money market funds",
        "How compound interest works — and why starting at 25 vs 35 makes a massive difference",
        "What are ETFs and why they're perfect for beginner investors",
        "How to evaluate if a stock is overvalued or undervalued",
        "Understanding inflation and how it silently eats your savings",
        "What is a REIT and how you can invest in real estate without buying property",
        "How central bank interest rates affect your loans, savings, and investments",
        "The power of an emergency fund — how much you actually need",
        "Understanding forex: what moves currency exchange rates",
        "What are dividends and how to build a dividend income portfolio",
        "Crypto basics: blockchain, Bitcoin, and why people are skeptical",
        "How to read a basic financial statement (balance sheet, income statement)",
        "Tax-efficient investing: legal ways to keep more of your returns",
    ],
    "cooking": [
        "The Maillard reaction: why browning food makes it taste incredible",
        "How to properly season a cast iron pan (and why it's worth it)",
        "The 5 mother sauces of French cooking and how they form the base of almost everything",
        "Why you should let meat rest after cooking (and how long)",
        "The science of emulsification: how to make a vinaigrette that doesn't separate",
        "Knife skills 101: the difference between dice, mince, julienne, and chiffonade",
        "How salt actually works in cooking (hint: it's not just about making things salty)",
        "The secret to perfect rice every time — the finger method and beyond",
        "How to balance flavors: sweet, salty, sour, bitter, and umami",
        "Why some recipes call for room temperature eggs and butter",
        "Fermentation basics: how kimchi, yogurt, and sourdough all use the same principle",
        "The difference between baking soda and baking powder (and when to use which)",
        "How to properly deglaze a pan and why fond is liquid gold",
        "Understanding smoke points: which oils to use for frying vs dressing",
        "The art of slow cooking: why low and slow beats high and fast for tough cuts",
    ],
    "film": [
        "What is mise-en-scène and why it separates good directors from great ones",
        "The rule of thirds in cinematography — how framing tells a story",
        "Why some movies use long takes (and the technical nightmare of pulling them off)",
        "The history of the jump cut and how the French New Wave changed editing forever",
        "What makes a great movie score — the psychology of film music",
        "The difference between a director's cut and a theatrical release",
        "Color grading: how movies use color palettes to set mood (think: The Matrix green tint)",
        "What is method acting and why some actors take it too far",
        "How the Kuleshov effect proves that editing creates meaning, not just footage",
        "The three-act structure: why 90% of movies follow the same storytelling template",
        "Practical effects vs CGI: when each works best (and worst)",
        "What makes a plot twist work — the difference between surprise and cheap tricks",
        "How sound design makes horror movies terrifying (it's not just the music)",
        "The auteur theory: when a director's personal style becomes bigger than any single film",
        "Why some great films flopped at the box office — commercial success vs artistic merit",
    ],
}

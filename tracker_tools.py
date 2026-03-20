import os
import logging
import certifi
from pymongo import MongoClient, ASCENDING
from pymongo.errors import PyMongoError
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pytz

load_dotenv()
logger = logging.getLogger(__name__)

EAT_ZONE = pytz.timezone('Africa/Nairobi')

# --- CONNECT TO MONGODB ---
db = None
budgets_col = None
income_col = None
portfolio_col = None
reminders_col = None
server_settings_col = None
todos_col = None
journal_col = None

try:
    mongo_client = MongoClient(
        os.getenv("MONGO_URI"),
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
    )
    mongo_client.admin.command('ping')
    db = mongo_client["emily_brain_db"]
    budgets_col = db["budgets"]
    income_col = db["income"]
    portfolio_col = db["portfolios"]
    reminders_col = db["reminders"]
    server_settings_col = db["server_settings"]
    todos_col = db["todos"]
    journal_col = db["journal"]

    # Indexes
    budgets_col.create_index([("user_id", ASCENDING), ("date", ASCENDING)])
    income_col.create_index([("user_id", ASCENDING), ("date", ASCENDING)])
    portfolio_col.create_index([("user_id", ASCENDING)])
    reminders_col.create_index([("remind_at", ASCENDING), ("status", ASCENDING)])
    server_settings_col.create_index([("guild_id", ASCENDING)])
    todos_col.create_index([("user_id", ASCENDING), ("status", ASCENDING)])
    journal_col.create_index([("user_id", ASCENDING), ("date", ASCENDING)])

    logger.info("Tracker tools connected to MongoDB!")
except Exception as e:
    logger.error(f"Tracker MongoDB error: {e}")


def _now():
    return datetime.now(EAT_ZONE)


# ══════════════════════════════════════════════
# BUDGET TRACKER
# ══════════════════════════════════════════════
def log_expense(user_id, amount, description, category="general"):
    """Log a spending entry."""
    if budgets_col is None:
        return False
    try:
        now = _now()
        budgets_col.insert_one({
            "user_id": str(user_id),
            "amount": float(amount),
            "description": description,
            "category": category,
            "date": now,
            "date_str": now.strftime("%Y-%m-%d"),
            "month_str": now.strftime("%Y-%m"),
        })
        logger.info(f"Expense logged for {user_id}: KES {amount} - {description}")
        return True
    except PyMongoError as e:
        logger.error(f"Budget log error: {e}")
        return False


def recategorize_expenses(user_id, category_func, month_str=None):
    """Re-apply category detection to all expenses for a user.
    category_func: callable(description) -> category string
    Returns: dict with counts of changes per category
    """
    if budgets_col is None:
        return None
    try:
        if not month_str:
            month_str = _now().strftime("%Y-%m")

        entries = list(budgets_col.find({
            "user_id": str(user_id),
            "month_str": month_str,
        }))

        changes = {"total": len(entries), "updated": 0, "by_category": {}}

        for entry in entries:
            old_cat = entry.get("category", "general")
            desc = entry.get("description", "")
            new_cat = category_func(desc)

            if new_cat != old_cat:
                budgets_col.update_one(
                    {"_id": entry["_id"]},
                    {"$set": {"category": new_cat}}
                )
                changes["updated"] += 1
                key = f"{old_cat} → {new_cat}"
                changes["by_category"][key] = changes["by_category"].get(key, 0) + 1

        logger.info(f"Recategorized {changes['updated']}/{changes['total']} expenses for {user_id}")
        return changes
    except PyMongoError as e:
        logger.error(f"Recategorize error: {e}")
        return None


def get_daily_spending(user_id, date_str=None):
    """Get total spending for a specific day (default: today)."""
    if budgets_col is None:
        return None
    try:
        if not date_str:
            date_str = _now().strftime("%Y-%m-%d")
        entries = list(budgets_col.find({
            "user_id": str(user_id),
            "date_str": date_str,
        }).sort("date", 1))

        total = sum(e["amount"] for e in entries)
        return {"entries": entries, "total": total, "date": date_str}
    except PyMongoError as e:
        logger.error(f"Budget fetch error: {e}")
        return None


def get_monthly_spending(user_id, month_str=None):
    """Get total spending for a month (default: current month)."""
    if budgets_col is None:
        return None
    try:
        if not month_str:
            month_str = _now().strftime("%Y-%m")
        entries = list(budgets_col.find({
            "user_id": str(user_id),
            "month_str": month_str,
        }).sort("date", 1))

        total = sum(e["amount"] for e in entries)

        # Group by category
        by_category = {}
        for e in entries:
            cat = e.get("category", "general")
            by_category[cat] = by_category.get(cat, 0) + e["amount"]

        # Group by day
        by_day = {}
        for e in entries:
            day = e["date_str"]
            by_day[day] = by_day.get(day, 0) + e["amount"]

        return {
            "entries": entries,
            "total": total,
            "by_category": by_category,
            "by_day": by_day,
            "month": month_str,
            "count": len(entries),
        }
    except PyMongoError as e:
        logger.error(f"Monthly budget error: {e}")
        return None


def set_budget_limit(user_id, monthly_limit):
    """Set a monthly budget limit for a user."""
    if budgets_col is None:
        return False
    try:
        db["budget_limits"].update_one(
            {"user_id": str(user_id)},
            {"$set": {"monthly_limit": float(monthly_limit), "updated_at": _now()}},
            upsert=True,
        )
        return True
    except PyMongoError as e:
        logger.error(f"Budget limit error: {e}")
        return False


def get_budget_limit(user_id):
    """Get user's monthly budget limit."""
    try:
        doc = db["budget_limits"].find_one({"user_id": str(user_id)})
        return doc.get("monthly_limit") if doc else None
    except Exception:
        return None


def format_budget_summary(user_id):
    """Generate a formatted budget summary for Emily to relay."""
    daily = get_daily_spending(user_id)
    monthly = get_monthly_spending(user_id)
    limit = get_budget_limit(user_id)

    if not daily and not monthly:
        return "No spending recorded yet! Start logging with `!spent 500 lunch at Java` and I'll track everything for you, manze."

    lines = []
    lines.append(f"📊 **Your Money This Month**\n")

    # Today
    if daily:
        lines.append(f"**Today ({daily['date']}):** KES {daily['total']:,.2f}")
        if daily['entries']:
            for e in daily['entries'][-5:]:
                lines.append(f"  • {e['description']}: KES {e['amount']:,.2f}")

    # This month
    if monthly:
        lines.append(f"\n**{monthly['month']} total:** KES {monthly['total']:,.2f} ({monthly['count']} transactions)")
        if monthly['by_category']:
            lines.append("**Where it went:**")
            for cat, amt in sorted(monthly['by_category'].items(), key=lambda x: -x[1]):
                pct = (amt / monthly['total'] * 100) if monthly['total'] > 0 else 0
                lines.append(f"  • {cat.title()}: KES {amt:,.2f} ({pct:.0f}%)")

    # Budget limit with Emily commentary
    if limit and monthly:
        remaining = limit - monthly['total']
        pct = (monthly['total'] / limit) * 100
        lines.append(f"\n**Budget:** KES {monthly['total']:,.2f} / KES {limit:,.2f} ({pct:.0f}%)")
        if remaining > 0:
            try:
                days_left = (datetime(int(monthly['month'][:4]), int(monthly['month'][5:]) + 1, 1, tzinfo=EAT_ZONE) - _now()).days
            except ValueError:
                days_left = 15
            if days_left > 0:
                daily_allowance = remaining / days_left
                lines.append(f"**Remaining:** KES {remaining:,.2f} (~KES {daily_allowance:,.0f}/day for {days_left} days)")

            if pct < 50:
                lines.append(f"\n_Fiti! You're doing well — keep it up, manze._ 💪")
            elif pct < 80:
                lines.append(f"\n_Sawa, you're on track but watch the spending. {days_left} days to go._ 👀")
            else:
                lines.append(f"\n_Eish, budget is getting tight! Time to be strategic with what's left._ ⚠️")
        else:
            lines.append(f"⚠️ **Over budget by KES {abs(remaining):,.2f}!**")
            lines.append(f"\n_Manze, we need to talk. Time to tighten up._ 😬")
    elif not limit:
        lines.append(f"\n_💡 Set a budget with `!setbudget 50000` and I'll help you stay on track._")

    return "\n".join(lines)


# ══════════════════════════════════════════════
# INCOME TRACKER
# ══════════════════════════════════════════════
INCOME_CATEGORIES = {
    "freelance": "💻 Freelance",
    "salary": "💼 Salary",
    "mpesa": "📱 M-Pesa",
    "gift": "🎁 Gift",
    "refund": "🔄 Refund",
    "side_hustle": "🛠️ Side Hustle",
    "investment": "📈 Investment",
    "other": "💰 Other",
}


def log_income(user_id, amount, source="freelance", description=""):
    """Log an income entry."""
    if income_col is None:
        return False
    try:
        now = _now()
        income_col.insert_one({
            "user_id": str(user_id),
            "amount": float(amount),
            "source": source.lower(),
            "description": description,
            "date": now,
            "date_str": now.strftime("%Y-%m-%d"),
            "month_str": now.strftime("%Y-%m"),
        })
        logger.info(f"Income logged for {user_id}: KES {amount} - {source} ({description})")
        return True
    except PyMongoError as e:
        logger.error(f"Income log error: {e}")
        return False


def get_monthly_income(user_id, month_str=None):
    """Get total income for a month (default: current month)."""
    if income_col is None:
        return None
    try:
        if not month_str:
            month_str = _now().strftime("%Y-%m")
        entries = list(income_col.find({
            "user_id": str(user_id),
            "month_str": month_str,
        }).sort("date", 1))

        total = sum(e["amount"] for e in entries)

        by_source = {}
        for e in entries:
            src = e.get("source", "other")
            by_source[src] = by_source.get(src, 0) + e["amount"]

        return {
            "entries": entries,
            "total": total,
            "by_source": by_source,
            "month": month_str,
            "count": len(entries),
        }
    except PyMongoError as e:
        logger.error(f"Monthly income error: {e}")
        return None


def delete_last_income(user_id):
    """Delete the most recent income entry for a user."""
    if income_col is None:
        return None
    try:
        last_entry = income_col.find_one(
            {"user_id": str(user_id)},
            sort=[("date", -1)]
        )
        if not last_entry:
            return None

        income_col.delete_one({"_id": last_entry["_id"]})
        logger.info(f"Deleted income for {user_id}: KES {last_entry['amount']}")
        return last_entry
    except PyMongoError as e:
        logger.error(f"Income delete error: {e}")
        return None


def get_effective_budget(user_id):
    """
    Calculate the effective monthly budget.
    Base budget + any income logged this month.
    - If user has a fixed budget AND income: budget + income
    - If only income: use income as the budget
    - If only fixed limit: use that
    """
    limit = get_budget_limit(user_id)
    monthly_income = get_monthly_income(user_id)
    income_total = monthly_income["total"] if monthly_income else 0

    if income_total > 0 and limit:
        return limit + income_total
    elif income_total > 0:
        return income_total
    elif limit:
        return limit
    return None


# ══════════════════════════════════════════════
# BUDGET SUMMARY (with income)
# ══════════════════════════════════════════════
def format_full_budget_summary(user_id):
    """Generate a formatted budget summary including income."""
    daily = get_daily_spending(user_id)
    monthly = get_monthly_spending(user_id)
    monthly_income = get_monthly_income(user_id)
    limit = get_budget_limit(user_id)

    if not daily and not monthly and not monthly_income:
        return ("No financial data recorded yet!\n"
                "• Log income: `!income 50000 freelance web project`\n"
                "• Log spending: `!spent 500 lunch at Java`\n"
                "• Set budget: `!setbudget 50000`")

    lines = [f"📊 **Your Money This Month**\n"]

    # ── Income section ──
    if monthly_income and monthly_income["total"] > 0:
        lines.append(f"💰 **Income:** KES {monthly_income['total']:,.2f} ({monthly_income['count']} entries)")
        if monthly_income["by_source"]:
            for src, amt in sorted(monthly_income["by_source"].items(), key=lambda x: -x[1]):
                label = INCOME_CATEGORIES.get(src, f"💰 {src.title()}")
                lines.append(f"  • {label}: KES {amt:,.2f}")
        lines.append("")

    # ── Today's spending ──
    if daily and daily["total"] > 0:
        lines.append(f"**Today ({daily['date']}):** KES {daily['total']:,.2f}")
        if daily["entries"]:
            for e in daily["entries"][-5:]:
                lines.append(f"  • {e['description']}: KES {e['amount']:,.2f}")
        lines.append("")

    # ── Monthly spending ──
    if monthly and monthly["total"] > 0:
        lines.append(f"📉 **Spent:** KES {monthly['total']:,.2f} ({monthly['count']} transactions)")
        if monthly["by_category"]:
            lines.append("**Where it went:**")
            for cat, amt in sorted(monthly["by_category"].items(), key=lambda x: -x[1]):
                pct = (amt / monthly["total"] * 100) if monthly["total"] > 0 else 0
                lines.append(f"  • {cat.title()}: KES {amt:,.2f} ({pct:.0f}%)")
        lines.append("")

    # ── Balance / Budget ──
    income_total = monthly_income["total"] if monthly_income else 0
    spent_total = monthly["total"] if monthly else 0
    effective_budget = get_effective_budget(user_id)

    if income_total > 0:
        balance = income_total - spent_total
        lines.append(f"💵 **Balance:** KES {balance:,.2f} (Income - Expenses)")

    if effective_budget and effective_budget > 0:
        remaining = effective_budget - spent_total
        pct = (spent_total / effective_budget) * 100

        budget_label = "Income" if income_total > 0 and not limit else "Budget"
        lines.append(f"📋 **{budget_label} usage:** KES {spent_total:,.2f} / KES {effective_budget:,.2f} ({pct:.0f}%)")

        if remaining > 0:
            try:
                month_str = monthly["month"] if monthly else _now().strftime("%Y-%m")
                year, month = int(month_str[:4]), int(month_str[5:])
                if month == 12:
                    next_month = datetime(year + 1, 1, 1, tzinfo=EAT_ZONE)
                else:
                    next_month = datetime(year, month + 1, 1, tzinfo=EAT_ZONE)
                days_left = (next_month - _now()).days
            except (ValueError, TypeError):
                days_left = 15

            if days_left > 0:
                daily_allowance = remaining / days_left
                lines.append(f"**Remaining:** KES {remaining:,.2f} (~KES {daily_allowance:,.0f}/day for {days_left} days)")

            if pct < 50:
                lines.append(f"\n_Fiti! You're doing well — keep it up, manze._ 💪")
            elif pct < 80:
                lines.append(f"\n_Sawa, you're on track but watch the spending. {days_left} days to go._ 👀")
            else:
                lines.append(f"\n_Eish, budget is getting tight! Time to be strategic with what's left._ ⚠️")
        else:
            lines.append(f"⚠️ **Over budget by KES {abs(remaining):,.2f}!**")
            lines.append(f"\n_Manze, we need to talk. Time to tighten up._ 😬")
    elif not effective_budget:
        lines.append(f"\n_💡 Log income with `!income 50000 freelance` or set a budget with `!setbudget 50000`_")

    return "\n".join(lines)


# ══════════════════════════════════════════════
# PORTFOLIO TRACKER
# ══════════════════════════════════════════════
def add_holding(user_id, ticker, shares, buy_price, notes=""):
    """Add a stock holding to portfolio."""
    if portfolio_col is None:
        return False
    try:
        portfolio_col.update_one(
            {"user_id": str(user_id), "ticker": ticker.upper()},
            {
                "$set": {
                    "ticker": ticker.upper(),
                    "shares": float(shares),
                    "buy_price": float(buy_price),
                    "notes": notes,
                    "updated_at": _now(),
                },
                "$setOnInsert": {
                    "user_id": str(user_id),
                    "added_at": _now(),
                }
            },
            upsert=True,
        )
        logger.info(f"Portfolio: {user_id} added {shares} shares of {ticker} at {buy_price}")
        return True
    except PyMongoError as e:
        logger.error(f"Portfolio add error: {e}")
        return False


def remove_holding(user_id, ticker):
    """Remove a stock from portfolio."""
    if portfolio_col is None:
        return False
    try:
        result = portfolio_col.delete_one({
            "user_id": str(user_id),
            "ticker": ticker.upper(),
        })
        return result.deleted_count > 0
    except PyMongoError as e:
        logger.error(f"Portfolio remove error: {e}")
        return False


def get_portfolio(user_id):
    """Get all holdings for a user."""
    if portfolio_col is None:
        return []
    try:
        holdings = list(portfolio_col.find(
            {"user_id": str(user_id)},
            {"_id": 0, "ticker": 1, "shares": 1, "buy_price": 1, "notes": 1, "added_at": 1}
        ))
        return holdings
    except PyMongoError as e:
        logger.error(f"Portfolio fetch error: {e}")
        return []


def format_portfolio(user_id):
    """Generate formatted portfolio summary."""
    holdings = get_portfolio(user_id)
    if not holdings:
        return "Your portfolio is empty. Tell me what stocks you own!"

    lines = ["📈 **Your Portfolio**\n"]
    total_invested = 0

    for h in holdings:
        ticker = h["ticker"]
        shares = h["shares"]
        price = h["buy_price"]
        invested = shares * price
        total_invested += invested
        notes = f" ({h['notes']})" if h.get("notes") else ""
        lines.append(f"• **{ticker}**: {shares:.0f} shares @ KES {price:,.2f} = KES {invested:,.2f}{notes}")

    lines.append(f"\n**Total invested:** KES {total_invested:,.2f}")
    lines.append(f"**Holdings:** {len(holdings)} stocks")
    lines.append("\n*Use [STOCK: TICKER] tags to check current prices against your buy prices!*")
    return "\n".join(lines)


# ══════════════════════════════════════════════
# REMINDERS
# ══════════════════════════════════════════════
def add_reminder(user_id, channel_id, remind_at, text):
    """Schedule a reminder."""
    if reminders_col is None:
        return False
    try:
        reminders_col.insert_one({
            "user_id": str(user_id),
            "channel_id": str(channel_id),
            "remind_at": remind_at,
            "text": text,
            "status": "pending",
            "created_at": _now(),
        })
        logger.info(f"Reminder set for {user_id}: {text} at {remind_at}")
        return True
    except PyMongoError as e:
        logger.error(f"Reminder add error: {e}")
        return False


def get_due_reminders():
    """Get all pending reminders that are due."""
    if reminders_col is None:
        return []
    try:
        now = _now()
        return list(reminders_col.find({
            "remind_at": {"$lte": now},
            "status": "pending",
        }))
    except PyMongoError as e:
        logger.error(f"Reminder fetch error: {e}")
        return []


def mark_reminder_done(reminder_id):
    """Mark a reminder as sent."""
    if reminders_col is None:
        return
    try:
        reminders_col.update_one(
            {"_id": reminder_id},
            {"$set": {"status": "sent", "sent_at": _now()}}
        )
    except PyMongoError as e:
        logger.error(f"Reminder mark error: {e}")


def get_user_reminders(user_id):
    """Get all pending reminders for a user."""
    if reminders_col is None:
        return []
    try:
        return list(reminders_col.find({
            "user_id": str(user_id),
            "status": "pending",
        }).sort("remind_at", 1))
    except PyMongoError as e:
        logger.error(f"Reminder list error: {e}")
        return []


# ══════════════════════════════════════════════
# SERVER SETTINGS (multi-server support)
# ══════════════════════════════════════════════
def get_server_settings(guild_id):
    """Get settings for a Discord server."""
    if server_settings_col is None:
        return _default_settings(guild_id)
    try:
        doc = server_settings_col.find_one({"guild_id": str(guild_id)})
        if doc:
            return doc
        return _default_settings(guild_id)
    except PyMongoError as e:
        logger.error(f"Server settings fetch error: {e}")
        return _default_settings(guild_id)


def update_server_setting(guild_id, key, value):
    """Update a single setting for a server."""
    if server_settings_col is None:
        return False
    try:
        server_settings_col.update_one(
            {"guild_id": str(guild_id)},
            {"$set": {key: value, "updated_at": _now()}},
            upsert=True,
        )
        return True
    except PyMongoError as e:
        logger.error(f"Server settings update error: {e}")
        return False


def _default_settings(guild_id):
    return {
        "guild_id": str(guild_id),
        "news_channel_id": None,
        "news_enabled": False,
        "news_time": "07:00",  # EAT
        "news_topics": ["Kenya", "Africa", "business", "technology"],
        "language": "en",
    }


def set_news_channel(guild_id, channel_id):
    """Set the channel for daily news briefings."""
    return update_server_setting(guild_id, "news_channel_id", str(channel_id)) and \
           update_server_setting(guild_id, "news_enabled", True)


def get_news_servers():
    """Get all servers with news enabled."""
    if server_settings_col is None:
        return []
    try:
        return list(server_settings_col.find({"news_enabled": True}))
    except PyMongoError as e:
        logger.error(f"News servers fetch error: {e}")
        return []


# ══════════════════════════════════════════════
# CUSTOM SERVER PERSONA
# ══════════════════════════════════════════════
PERSONA_PRESETS = {
    "default": None,  # Standard Emily
    "professional": "Speak in a more professional, formal tone. Less slang, more business-like. Still warm but corporate-appropriate.",
    "casual": "Be extra casual and relaxed. More slang, more jokes, super chill vibes.",
    "sarcastic": "Be witty and sarcastic (but never mean). Dry humor, clever comebacks, playful roasts.",
    "mentor": "Be a supportive mentor. Focus on teaching, encouraging growth, and giving detailed explanations. Patient and nurturing.",
    "hype": "Be extremely enthusiastic and hype! Lots of energy, exclamation marks, motivational vibes. Like a best friend who's always cheering you on.",
    "techbro": "Speak like a Nairobi tech scene insider. Reference startups, Silicon Savannah, iHub, Konza City, tech Twitter. Mix tech jargon with Sheng.",
}


def set_server_persona(guild_id, persona_text):
    """Set a custom persona modifier for a server."""
    return update_server_setting(guild_id, "custom_persona", persona_text)


def get_server_persona(guild_id):
    """Get the custom persona for a server."""
    if server_settings_col is None:
        return None
    try:
        doc = server_settings_col.find_one({"guild_id": str(guild_id)})
        return doc.get("custom_persona") if doc else None
    except PyMongoError as e:
        logger.error(f"Get server persona error: {e}")
        return None


# ══════════════════════════════════════════════
# INVESTMENT ALERTS
# ══════════════════════════════════════════════
def set_alert_settings(user_id, channel_id, threshold_pct=5.0, enabled=True):
    """Set investment alert preferences for a user."""
    if db is None:
        return False
    try:
        db["investment_alerts"].update_one(
            {"user_id": str(user_id)},
            {"$set": {
                "user_id": str(user_id),
                "channel_id": str(channel_id),
                "threshold_pct": float(threshold_pct),
                "enabled": enabled,
                "updated_at": _now(),
            }},
            upsert=True,
        )
        return True
    except PyMongoError as e:
        logger.error(f"Set alert error: {e}")
        return False


def get_alert_settings(user_id):
    """Get a user's alert settings."""
    if db is None:
        return None
    try:
        return db["investment_alerts"].find_one({"user_id": str(user_id)})
    except PyMongoError as e:
        logger.error(f"Get alert error: {e}")
        return None


def get_all_alert_users():
    """Get all users with alerts enabled."""
    if db is None:
        return []
    try:
        return list(db["investment_alerts"].find({"enabled": True}))
    except PyMongoError as e:
        logger.error(f"Get all alerts error: {e}")
        return []


def save_last_prices(user_id, prices):
    """Save last known prices for a user's portfolio (for change detection)."""
    if db is None:
        return False
    try:
        db["investment_alerts"].update_one(
            {"user_id": str(user_id)},
            {"$set": {"last_prices": prices, "last_check": _now()}},
        )
        return True
    except PyMongoError as e:
        logger.error(f"Save prices error: {e}")
        return False


def get_last_prices(user_id):
    """Get last saved prices for a user."""
    if db is None:
        return {}
    try:
        doc = db["investment_alerts"].find_one({"user_id": str(user_id)})
        return doc.get("last_prices", {}) if doc else {}
    except PyMongoError as e:
        logger.error(f"Get last prices error: {e}")
        return {}


def get_all_users_with_portfolios():
    """Get all user IDs that have portfolio holdings."""
    if portfolio_col is None:
        return []
    try:
        return portfolio_col.distinct("user_id")
    except PyMongoError as e:
        logger.error(f"Get portfolio users error: {e}")
        return []


# ══════════════════════════════════════════════
# VOICE CHAT MODE (per channel)
# ══════════════════════════════════════════════
def set_voice_chat_channel(guild_id, channel_id, enabled=True):
    """Set a channel as a voice-chat channel where Emily always replies with voice."""
    if server_settings_col is None:
        return False
    try:
        # Store as a list of voice chat channels per guild
        if enabled:
            server_settings_col.update_one(
                {"guild_id": str(guild_id)},
                {"$addToSet": {"voice_chat_channels": str(channel_id)},
                 "$set": {"updated_at": _now()}},
                upsert=True,
            )
        else:
            server_settings_col.update_one(
                {"guild_id": str(guild_id)},
                {"$pull": {"voice_chat_channels": str(channel_id)}},
            )
        return True
    except PyMongoError as e:
        logger.error(f"Set voice chat channel error: {e}")
        return False


def is_voice_chat_channel(guild_id, channel_id):
    """Check if a channel is set as a voice-chat channel."""
    if server_settings_col is None:
        return False
    try:
        doc = server_settings_col.find_one({"guild_id": str(guild_id)})
        if doc:
            channels = doc.get("voice_chat_channels", [])
            return str(channel_id) in channels
        return False
    except PyMongoError as e:
        logger.error(f"Check voice chat channel error: {e}")
        return False


# ══════════════════════════════════════════════
# NEWS DEDUPLICATION
# ══════════════════════════════════════════════
def save_sent_news(guild_id, urls):
    """Save URLs of news articles that were sent to a server."""
    if db is None:
        return
    try:
        now = _now()
        for url in urls:
            db["sent_news"].update_one(
                {"guild_id": str(guild_id), "url": url},
                {"$set": {
                    "guild_id": str(guild_id),
                    "url": url,
                    "sent_at": now,
                }},
                upsert=True,
            )
        # Clean up articles older than 7 days
        cutoff = now - timedelta(days=7)
        db["sent_news"].delete_many({"sent_at": {"$lt": cutoff}})
    except PyMongoError as e:
        logger.error(f"Save sent news error: {e}")


def get_sent_news_urls(guild_id, days=3):
    """Get URLs of articles sent in the last N days."""
    if db is None:
        return set()
    try:
        cutoff = _now() - timedelta(days=days)
        docs = db["sent_news"].find({
            "guild_id": str(guild_id),
            "sent_at": {"$gte": cutoff},
        })
        return {doc["url"] for doc in docs}
    except PyMongoError as e:
        logger.error(f"Get sent news error: {e}")
        return set()


# ══════════════════════════════════════════════
# TO-DO LIST
# ══════════════════════════════════════════════
def add_todo(user_id, text, priority="normal"):
    """Add a to-do item."""
    if todos_col is None:
        return None
    try:
        result = todos_col.insert_one({
            "user_id": str(user_id),
            "text": text,
            "priority": priority,
            "status": "pending",
            "created_at": _now(),
            "completed_at": None,
        })
        # Return the position number
        count = todos_col.count_documents({
            "user_id": str(user_id),
            "status": "pending",
        })
        return count
    except PyMongoError as e:
        logger.error(f"Add todo error: {e}")
        return None


def complete_todo(user_id, index):
    """Mark a to-do as done by its position number (1-based)."""
    if todos_col is None:
        return None
    try:
        todos = list(todos_col.find({
            "user_id": str(user_id),
            "status": "pending",
        }).sort("created_at", 1))

        if index < 1 or index > len(todos):
            return None

        todo = todos[index - 1]
        todos_col.update_one(
            {"_id": todo["_id"]},
            {"$set": {"status": "done", "completed_at": _now()}}
        )
        return todo["text"]
    except PyMongoError as e:
        logger.error(f"Complete todo error: {e}")
        return None


def remove_todo(user_id, index):
    """Delete a to-do item by position number (1-based)."""
    if todos_col is None:
        return None
    try:
        todos = list(todos_col.find({
            "user_id": str(user_id),
            "status": "pending",
        }).sort("created_at", 1))

        if index < 1 or index > len(todos):
            return None

        todo = todos[index - 1]
        todos_col.delete_one({"_id": todo["_id"]})
        return todo["text"]
    except PyMongoError as e:
        logger.error(f"Remove todo error: {e}")
        return None


def get_todos(user_id, include_done=False):
    """Get all to-do items for a user."""
    if todos_col is None:
        return []
    try:
        query = {"user_id": str(user_id)}
        if not include_done:
            query["status"] = "pending"

        return list(todos_col.find(query).sort("created_at", 1))
    except PyMongoError as e:
        logger.error(f"Get todos error: {e}")
        return []


def clear_done_todos(user_id):
    """Remove all completed to-do items."""
    if todos_col is None:
        return 0
    try:
        result = todos_col.delete_many({
            "user_id": str(user_id),
            "status": "done",
        })
        return result.deleted_count
    except PyMongoError as e:
        logger.error(f"Clear todos error: {e}")
        return 0


def format_todos(todos):
    """Format to-do list for Discord display."""
    if not todos:
        return "📝 Your to-do list is empty! Add something with `!todo buy groceries` or just tell me naturally."

    lines = ["📝 **Your To-Do List**\n"]
    pending = [t for t in todos if t.get("status") == "pending"]
    done = [t for t in todos if t.get("status") == "done"]

    for i, t in enumerate(pending, 1):
        priority_icon = "🔴" if t.get("priority") == "high" else "🟡" if t.get("priority") == "medium" else "⬜"
        lines.append(f"{priority_icon} **{i}.** {t['text']}")

    if done:
        lines.append(f"\n✅ **Completed ({len(done)}):**")
        for t in done[-5:]:  # Show last 5 completed
            lines.append(f"~~{t['text']}~~")

    lines.append(f"\n_Mark done: `!done 1` | Remove: `!deltodo 1` | Clear done: `!cleartodos`_")
    return "\n".join(lines)


# ══════════════════════════════════════════════
# CANCEL REMINDER
# ══════════════════════════════════════════════
def cancel_reminder(user_id, index):
    """Cancel a pending reminder by its position number (1-based)."""
    if reminders_col is None:
        return None
    try:
        reminders = list(reminders_col.find({
            "user_id": str(user_id),
            "status": "pending",
        }).sort("remind_at", 1))

        if index < 1 or index > len(reminders):
            return None

        reminder = reminders[index - 1]
        reminders_col.delete_one({"_id": reminder["_id"]})
        return reminder["text"]
    except PyMongoError as e:
        logger.error(f"Cancel reminder error: {e}")
        return None


# ══════════════════════════════════════════════
# PERSONAL JOURNAL / MOOD TRACKER
# ══════════════════════════════════════════════
MOOD_SCALE = {
    1: ("😢", "terrible"),
    2: ("😔", "rough"),
    3: ("😐", "okay"),
    4: ("😊", "good"),
    5: ("🤩", "amazing"),
}

MOOD_KEYWORDS = {
    5: ["amazing", "incredible", "best day", "promoted", "got the job", "engaged", "married",
        "wonderful", "fantastic", "blessed", "thrilled", "ecstatic", "overjoyed", "won"],
    4: ["great", "good day", "happy", "proud", "grateful", "excited", "fun", "enjoyed",
        "accomplished", "productive", "awesome", "nice", "pleasant", "relaxed", "peaceful"],
    3: ["okay", "fine", "alright", "normal", "regular", "average", "meh", "not bad",
        "so-so", "usual", "same", "nothing special"],
    2: ["tough", "hard", "difficult", "stressed", "tired", "frustrated", "annoyed",
        "disappointed", "lonely", "anxious", "worried", "sad", "bad day", "rough",
        "exhausting", "overwhelming", "struggling"],
    1: ["terrible", "awful", "worst", "crying", "depressed", "heartbroken", "devastated",
        "hopeless", "miserable", "broke down", "can't cope", "falling apart"],
}


def detect_mood(text):
    """Auto-detect mood score (1-5) from journal text."""
    text_lower = text.lower()
    # Check from extreme moods inward
    for score in [1, 5, 2, 4, 3]:
        for keyword in MOOD_KEYWORDS[score]:
            if keyword in text_lower:
                return score
    return 3  # Default to neutral


def add_journal_entry(user_id, text, mood_score=None, tags=None):
    """Add a journal entry with mood tracking."""
    if journal_col is None:
        return None
    try:
        if mood_score is None:
            mood_score = detect_mood(text)

        mood_score = max(1, min(5, mood_score))
        mood_emoji, mood_label = MOOD_SCALE.get(mood_score, ("😐", "okay"))

        now = _now()
        entry = {
            "user_id": str(user_id),
            "text": text,
            "mood_score": mood_score,
            "mood_emoji": mood_emoji,
            "mood_label": mood_label,
            "tags": tags or [],
            "photos": [],
            "pinned": False,
            "date": now,
            "date_str": now.strftime("%Y-%m-%d"),
            "time_str": now.strftime("%I:%M %p"),
            "day_name": now.strftime("%A"),
            "source": "discord",
        }
        journal_col.insert_one(entry)
        logger.info(f"Journal entry for {user_id}: mood={mood_score} ({mood_label})")
        return entry
    except PyMongoError as e:
        logger.error(f"Journal entry error: {e}")
        return None


def get_journal_entries(user_id, days=7, limit=10):
    """Get recent journal entries."""
    if journal_col is None:
        return []
    try:
        cutoff = _now() - timedelta(days=days)
        return list(journal_col.find({
            "user_id": str(user_id),
            "date": {"$gte": cutoff},
        }).sort("date", -1).limit(limit))
    except PyMongoError as e:
        logger.error(f"Get journal error: {e}")
        return []


def get_mood_trend(user_id, days=14):
    """Get mood scores over time for trend analysis."""
    if journal_col is None:
        return []
    try:
        cutoff = _now() - timedelta(days=days)
        entries = list(journal_col.find({
            "user_id": str(user_id),
            "date": {"$gte": cutoff},
        }).sort("date", 1))

        # Group by day, average the mood
        daily_moods = {}
        for e in entries:
            day = e.get("date_str", "")
            if day not in daily_moods:
                daily_moods[day] = []
            daily_moods[day].append(e.get("mood_score", 3))

        trend = []
        for day, scores in sorted(daily_moods.items()):
            avg = sum(scores) / len(scores)
            trend.append({
                "date": day,
                "avg_mood": round(avg, 1),
                "entries": len(scores),
            })
        return trend
    except PyMongoError as e:
        logger.error(f"Mood trend error: {e}")
        return []


def get_mood_stats(user_id, days=30):
    """Get mood statistics for a period."""
    if journal_col is None:
        return None
    try:
        cutoff = _now() - timedelta(days=days)
        entries = list(journal_col.find({
            "user_id": str(user_id),
            "date": {"$gte": cutoff},
        }))

        if not entries:
            return None

        scores = [e.get("mood_score", 3) for e in entries]
        avg_mood = sum(scores) / len(scores)
        best_day = max(entries, key=lambda e: e.get("mood_score", 0))
        worst_day = min(entries, key=lambda e: e.get("mood_score", 5))

        # Count by mood level
        mood_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for s in scores:
            mood_dist[s] = mood_dist.get(s, 0) + 1

        return {
            "total_entries": len(entries),
            "avg_mood": round(avg_mood, 1),
            "best_day": best_day,
            "worst_day": worst_day,
            "mood_distribution": mood_dist,
            "streak": _get_journal_streak(user_id),
        }
    except PyMongoError as e:
        logger.error(f"Mood stats error: {e}")
        return None


def _get_journal_streak(user_id):
    """Calculate how many consecutive days the user has journaled."""
    if journal_col is None:
        return 0
    try:
        entries = list(journal_col.find({
            "user_id": str(user_id),
        }).sort("date", -1).limit(60))

        if not entries:
            return 0

        dates = sorted(set(e.get("date_str", "") for e in entries), reverse=True)
        if not dates:
            return 0

        today = _now().strftime("%Y-%m-%d")
        yesterday = (_now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # Streak must include today or yesterday
        if dates[0] != today and dates[0] != yesterday:
            return 0

        streak = 1
        for i in range(1, len(dates)):
            prev = datetime.strptime(dates[i - 1], "%Y-%m-%d")
            curr = datetime.strptime(dates[i], "%Y-%m-%d")
            if (prev - curr).days == 1:
                streak += 1
            else:
                break
        return streak
    except Exception:
        return 0


def format_journal_entries(entries):
    """Format journal entries for Discord display."""
    if not entries:
        return "📓 No journal entries yet. Start with `!journal I had a great day!` or just tell Emily about your day."

    lines = ["📓 **Your Journal**\n"]
    for e in entries:
        mood = e.get("mood_emoji", "😐")
        date = e.get("date_str", "")
        time = e.get("time_str", "")
        day = e.get("day_name", "")
        text = e.get("text", "")
        if len(text) > 120:
            text = text[:118] + ".."
        lines.append(f"{mood} **{day}, {date}** at {time}")
        lines.append(f"  {text}\n")

    return "\n".join(lines)


def format_mood_trend(trend):
    """Format mood trend as a visual chart for Discord."""
    if not trend:
        return "📊 Not enough data for a mood trend yet. Keep journaling!"

    lines = ["📊 **Your Mood Trend**\n"]

    for day in trend:
        avg = day["avg_mood"]
        date = day["date"]
        # Visual bar
        filled = round(avg)
        bar = "█" * filled + "░" * (5 - filled)
        emoji = MOOD_SCALE.get(filled, ("😐", "okay"))[0]

        # Shorten date
        try:
            short = datetime.strptime(date, "%Y-%m-%d").strftime("%b %d")
        except Exception:
            short = date

        lines.append(f"{emoji} `{bar}` {avg}/5 — {short} ({day['entries']} entries)")

    # Overall average
    all_scores = [d["avg_mood"] for d in trend]
    overall = sum(all_scores) / len(all_scores)
    overall_emoji = MOOD_SCALE.get(round(overall), ("😐", "okay"))[0]
    lines.append(f"\n**Overall: {overall_emoji} {overall:.1f}/5**")

    return "\n".join(lines)

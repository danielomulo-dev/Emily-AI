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

    # Indexes
    budgets_col.create_index([("user_id", ASCENDING), ("date", ASCENDING)])
    income_col.create_index([("user_id", ASCENDING), ("date", ASCENDING)])
    portfolio_col.create_index([("user_id", ASCENDING)])
    reminders_col.create_index([("remind_at", ASCENDING), ("status", ASCENDING)])
    server_settings_col.create_index([("guild_id", ASCENDING)])

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


def get_effective_budget(user_id):
    """
    Calculate the effective monthly budget.
    If user has a fixed budget limit AND income, use the higher of the two.
    If only income, use income as the budget.
    If only fixed limit, use that.
    """
    limit = get_budget_limit(user_id)
    monthly_income = get_monthly_income(user_id)
    income_total = monthly_income["total"] if monthly_income else 0

    if income_total > 0 and limit:
        return max(limit, income_total)
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

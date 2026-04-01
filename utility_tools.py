import os
import io
import random
import logging
import calendar
import time
import requests
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)

EAT_ZONE = pytz.timezone('Africa/Nairobi')


# ══════════════════════════════════════════════
# CURRENCY CONVERTER (live rates, cached 1hr)
# ══════════════════════════════════════════════
CURRENCY_API_URL = "https://open.er-api.com/v6/latest/{base}"

# Cache: {base_currency: {"rates": {...}, "time": timestamp}}
_currency_cache = {}
_CURRENCY_CACHE_TTL = 3600  # 1 hour

# Common currency pairs for Kenya
CURRENCY_ALIASES = {
    "KSH": "KES", "KSHS": "KES", "SHILLING": "KES", "SHILLINGS": "KES", "BOB": "KES",
    "DOLLAR": "USD", "DOLLARS": "USD", "BUCK": "USD", "BUCKS": "USD",
    "EURO": "EUR", "EUROS": "EUR",
    "POUND": "GBP", "POUNDS": "GBP", "QUID": "GBP",
    "YEN": "JPY", "YUAN": "CNY", "RAND": "ZAR",
    "NAIRA": "NGN", "CEDI": "GHS", "SHILLING_UG": "UGX", "SHILLING_TZ": "TZS",
    "DIRHAM": "AED", "RIYAL": "SAR", "RUPEE": "INR", "RUPEES": "INR",
    "FRANC": "CHF", "BITCOIN": "BTC",
}


def convert_currency(amount, from_currency, to_currency):
    """Convert between currencies using live exchange rates (cached 1hr)."""
    try:
        from_c = CURRENCY_ALIASES.get(from_currency.upper(), from_currency.upper())
        to_c = CURRENCY_ALIASES.get(to_currency.upper(), to_currency.upper())

        # Check cache first
        cached = _currency_cache.get(from_c)
        if cached and time.time() - cached["time"] < _CURRENCY_CACHE_TTL:
            rates = cached["rates"]
            updated = cached.get("updated", "cached")
        else:
            response = requests.get(CURRENCY_API_URL.format(base=from_c), timeout=10)
            data = response.json()
            if data.get("result") != "success":
                return None, f"Couldn't fetch rates for {from_c}"
            rates = data.get("rates", {})
            updated = data.get("time_last_update_utc", "unknown")
            _currency_cache[from_c] = {"rates": rates, "updated": updated, "time": time.time()}

        if to_c not in rates:
            return None, f"Unknown currency: {to_c}"

        rate = rates[to_c]
        converted = amount * rate

        result = {
            "amount": amount,
            "from": from_c,
            "to": to_c,
            "rate": rate,
            "converted": converted,
            "updated": updated,
        }
        return result, None

    except Exception as e:
        logger.error(f"Currency conversion error: {e}")
        return None, f"Currency conversion failed"


def format_currency_result(result):
    """Format currency conversion for display."""
    if not result:
        return "Couldn't convert that."
    return (
        f"💱 **Currency Conversion**\n"
        f"**{result['amount']:,.2f} {result['from']}** = **{result['converted']:,.2f} {result['to']}**\n"
        f"Rate: 1 {result['from']} = {result['rate']:.4f} {result['to']}\n"
        f"_Updated: {result['updated'][:16]}_"
    )


# ══════════════════════════════════════════════
# LOAN / INTEREST CALCULATOR
# ══════════════════════════════════════════════
def calculate_loan(principal, annual_rate, months, loan_type="reducing"):
    """
    Calculate loan repayment details.
    loan_type: 'reducing' (standard) or 'flat' (common in Kenya for personal loans)
    """
    try:
        p = float(principal)
        r = float(annual_rate) / 100 / 12  # Monthly rate
        n = int(months)

        if loan_type == "flat":
            # Flat rate (common for M-Shwari, personal loans)
            total_interest = p * (float(annual_rate) / 100) * (n / 12)
            total_payment = p + total_interest
            monthly_payment = total_payment / n
            result = {
                "type": "Flat Rate",
                "principal": p,
                "annual_rate": float(annual_rate),
                "months": n,
                "monthly_payment": monthly_payment,
                "total_interest": total_interest,
                "total_payment": total_payment,
            }
        else:
            # Reducing balance (standard amortization)
            if r == 0:
                monthly_payment = p / n
            else:
                monthly_payment = p * (r * (1 + r)**n) / ((1 + r)**n - 1)
            total_payment = monthly_payment * n
            total_interest = total_payment - p
            result = {
                "type": "Reducing Balance",
                "principal": p,
                "annual_rate": float(annual_rate),
                "months": n,
                "monthly_payment": monthly_payment,
                "total_interest": total_interest,
                "total_payment": total_payment,
            }

        return result, None

    except Exception as e:
        logger.error(f"Loan calc error: {e}")
        return None, f"Calculation error: {e}"


def calculate_mshwari(principal, days=30):
    """Calculate M-Shwari loan cost (7.5% facility fee per 30 days)."""
    try:
        p = float(principal)
        fee_rate = 0.075  # 7.5% per 30 days
        periods = max(1, int(days) // 30)
        total_fee = p * fee_rate * periods
        total_repayment = p + total_fee

        return {
            "principal": p,
            "fee_rate": f"{fee_rate * 100}%",
            "days": days,
            "total_fee": total_fee,
            "total_repayment": total_repayment,
            "effective_annual_rate": fee_rate * 12 * 100,
        }, None

    except Exception as e:
        return None, f"M-Shwari calc error: {e}"


def format_loan_result(result):
    """Format loan calculation for display."""
    if not result:
        return "Couldn't calculate that."

    lines = [f"🏦 **Loan Calculator ({result['type']})**\n"]
    lines.append(f"**Principal:** KES {result['principal']:,.2f}")
    lines.append(f"**Interest Rate:** {result['annual_rate']:.1f}% p.a.")
    lines.append(f"**Duration:** {result['months']} months")
    lines.append(f"**Monthly Payment:** KES {result['monthly_payment']:,.2f}")
    lines.append(f"**Total Interest:** KES {result['total_interest']:,.2f}")
    lines.append(f"**Total Repayment:** KES {result['total_payment']:,.2f}")

    return "\n".join(lines)


def format_mshwari_result(result):
    """Format M-Shwari calculation."""
    if not result:
        return "Couldn't calculate that."
    return (
        f"📱 **M-Shwari Loan Calculator**\n\n"
        f"**Loan Amount:** KES {result['principal']:,.2f}\n"
        f"**Facility Fee:** {result['fee_rate']} per 30 days\n"
        f"**Duration:** {result['days']} days\n"
        f"**Total Fee:** KES {result['total_fee']:,.2f}\n"
        f"**Total Repayment:** KES {result['total_repayment']:,.2f}\n"
        f"**Effective Annual Rate:** {result['effective_annual_rate']:.1f}% 😬\n\n"
        f"_Manze, that's expensive. Consider alternatives like bank personal loans or SACCOs._"
    )


# ══════════════════════════════════════════════
# KENYAN BANK & SACCO LOAN CALCULATORS
# ══════════════════════════════════════════════

# Approximate rates as of 2025-2026 (rates change — these are estimates)
KENYAN_LENDERS = {
    # Mobile lending
    "mshwari": {"name": "M-Shwari", "type": "mobile", "rate_type": "flat_fee", "fee_per_30d": 7.5, "max_days": 30},
    "kcb-mpesa": {"name": "KCB M-Pesa", "type": "mobile", "rate_type": "flat_fee", "fee_per_30d": 8.64, "max_days": 30},
    "fuliza": {"name": "Fuliza", "type": "mobile", "rate_type": "daily_fee", "daily_rate": 0.5, "max_rate_per_day": 1.0},
    "tala": {"name": "Tala", "type": "mobile", "rate_type": "flat_fee", "fee_per_30d": 15.0, "max_days": 30},
    "branch": {"name": "Branch", "type": "mobile", "rate_type": "flat_fee", "fee_per_30d": 15.0, "max_days": 30},

    # Banks (annual reducing balance rates)
    "kcb": {"name": "KCB Bank", "type": "bank", "rate_type": "reducing", "annual_rate": 16.0, "min_months": 6, "max_months": 72},
    "equity": {"name": "Equity Bank", "type": "bank", "rate_type": "reducing", "annual_rate": 16.5, "min_months": 6, "max_months": 72},
    "coop": {"name": "Co-op Bank", "type": "bank", "rate_type": "reducing", "annual_rate": 16.0, "min_months": 6, "max_months": 60},
    "absa": {"name": "ABSA Kenya", "type": "bank", "rate_type": "reducing", "annual_rate": 15.5, "min_months": 6, "max_months": 60},
    "stanbic": {"name": "Stanbic Bank", "type": "bank", "rate_type": "reducing", "annual_rate": 16.0, "min_months": 6, "max_months": 60},
    "ncba": {"name": "NCBA Bank", "type": "bank", "rate_type": "reducing", "annual_rate": 16.0, "min_months": 6, "max_months": 60},
    "dtb": {"name": "DTB Bank", "type": "bank", "rate_type": "reducing", "annual_rate": 15.0, "min_months": 6, "max_months": 60},
    "family": {"name": "Family Bank", "type": "bank", "rate_type": "reducing", "annual_rate": 18.0, "min_months": 6, "max_months": 48},
    "im": {"name": "I&M Bank", "type": "bank", "rate_type": "reducing", "annual_rate": 15.0, "min_months": 6, "max_months": 60},

    # SACCOs (reducing balance, typically cheaper)
    "stima": {"name": "Stima SACCO", "type": "sacco", "rate_type": "reducing", "annual_rate": 12.0, "min_months": 1, "max_months": 72},
    "kenya-police": {"name": "Kenya Police SACCO", "type": "sacco", "rate_type": "reducing", "annual_rate": 12.0, "min_months": 1, "max_months": 60},
    "mwalimu": {"name": "Mwalimu National SACCO", "type": "sacco", "rate_type": "reducing", "annual_rate": 12.0, "min_months": 1, "max_months": 48},
    "harambee": {"name": "Harambee SACCO", "type": "sacco", "rate_type": "reducing", "annual_rate": 12.0, "min_months": 1, "max_months": 48},
    "unaitas": {"name": "Unaitas SACCO", "type": "sacco", "rate_type": "reducing", "annual_rate": 14.0, "min_months": 1, "max_months": 60},
    "ukulima": {"name": "Ukulima SACCO", "type": "sacco", "rate_type": "reducing", "annual_rate": 12.0, "min_months": 1, "max_months": 48},
}

# Aliases for easier lookup
LENDER_ALIASES = {
    "m-shwari": "mshwari", "mshwari": "mshwari",
    "kcb-mpesa": "kcb-mpesa", "kcbmpesa": "kcb-mpesa", "kcb mpesa": "kcb-mpesa",
    "fuliza": "fuliza",
    "tala": "tala", "branch": "branch",
    "kcb": "kcb", "kcb bank": "kcb",
    "equity": "equity", "equity bank": "equity",
    "coop": "coop", "co-op": "coop", "cooperative": "coop", "co-op bank": "coop",
    "absa": "absa", "barclays": "absa",
    "stanbic": "stanbic",
    "ncba": "ncba",
    "dtb": "dtb", "diamond trust": "dtb",
    "family": "family", "family bank": "family",
    "im": "im", "i&m": "im", "i&m bank": "im",
    "stima": "stima", "stima sacco": "stima",
    "kenya police": "kenya-police", "police sacco": "kenya-police",
    "mwalimu": "mwalimu", "mwalimu sacco": "mwalimu",
    "harambee": "harambee", "harambee sacco": "harambee",
    "unaitas": "unaitas", "unaitas sacco": "unaitas",
    "ukulima": "ukulima", "ukulima sacco": "ukulima",
}


def calculate_kenyan_loan(lender_key, principal, months=12):
    """Calculate loan for a specific Kenyan lender."""
    try:
        lender = KENYAN_LENDERS.get(lender_key)
        if not lender:
            return None, f"Unknown lender: {lender_key}"

        p = float(principal)
        n = int(months)

        if lender["rate_type"] == "flat_fee":
            # Mobile lenders — flat fee per 30 days
            fee_pct = lender["fee_per_30d"] / 100
            periods = max(1, n)  # months = periods
            total_fee = p * fee_pct * periods
            total_repayment = p + total_fee
            monthly_payment = total_repayment / max(periods, 1)
            effective_annual = fee_pct * 12 * 100

            return {
                "lender": lender["name"],
                "type": lender["type"],
                "rate_type": "Flat Fee",
                "principal": p,
                "fee_rate": f"{lender['fee_per_30d']}% per 30 days",
                "months": n,
                "monthly_payment": monthly_payment,
                "total_fee": total_fee,
                "total_repayment": total_repayment,
                "effective_annual": effective_annual,
            }, None

        elif lender["rate_type"] == "daily_fee":
            # Fuliza-style — daily charge
            daily_rate = lender["daily_rate"] / 100
            days = n * 30
            total_fee = p * daily_rate * days
            total_repayment = p + total_fee
            effective_annual = daily_rate * 365 * 100

            return {
                "lender": lender["name"],
                "type": lender["type"],
                "rate_type": "Daily Fee",
                "principal": p,
                "fee_rate": f"{lender['daily_rate']}% per day",
                "months": n,
                "days": days,
                "total_fee": total_fee,
                "total_repayment": total_repayment,
                "effective_annual": effective_annual,
            }, None

        elif lender["rate_type"] == "reducing":
            # Bank/SACCO — reducing balance
            annual_rate = lender["annual_rate"]
            r = annual_rate / 100 / 12
            if r == 0:
                monthly_payment = p / n
            else:
                monthly_payment = p * (r * (1 + r)**n) / ((1 + r)**n - 1)
            total_repayment = monthly_payment * n
            total_interest = total_repayment - p

            return {
                "lender": lender["name"],
                "type": lender["type"],
                "rate_type": "Reducing Balance",
                "principal": p,
                "annual_rate": annual_rate,
                "months": n,
                "monthly_payment": monthly_payment,
                "total_interest": total_interest,
                "total_repayment": total_repayment,
            }, None

    except Exception as e:
        return None, f"Calculation error: {e}"


def format_kenyan_loan(result):
    """Format Kenyan loan calculation."""
    if not result:
        return "Couldn't calculate that."

    type_emoji = {"mobile": "📱", "bank": "🏦", "sacco": "🤝"}.get(result["type"], "💰")
    lines = [f"{type_emoji} **{result['lender']} Loan Calculator**\n"]

    lines.append(f"**Loan Amount:** KES {result['principal']:,.2f}")

    if result["rate_type"] == "Reducing Balance":
        lines.append(f"**Interest Rate:** {result['annual_rate']}% p.a. (reducing balance)")
        lines.append(f"**Duration:** {result['months']} months")
        lines.append(f"**Monthly Payment:** KES {result['monthly_payment']:,.2f}")
        lines.append(f"**Total Interest:** KES {result['total_interest']:,.2f}")
        lines.append(f"**Total Repayment:** KES {result['total_repayment']:,.2f}")
    else:
        lines.append(f"**Fee:** {result['fee_rate']}")
        if result.get("days"):
            lines.append(f"**Duration:** {result['days']} days")
        else:
            lines.append(f"**Duration:** {result['months']} month(s)")
        lines.append(f"**Total Fee:** KES {result['total_fee']:,.2f}")
        lines.append(f"**Total Repayment:** KES {result['total_repayment']:,.2f}")
        lines.append(f"**Effective Annual Rate:** {result['effective_annual']:.1f}% 😬")

    return "\n".join(lines)


def compare_lenders(principal, months=12, lender_keys=None):
    """Compare multiple lenders for the same loan amount."""
    if not lender_keys:
        # Default: compare popular options
        lender_keys = ["mshwari", "kcb-mpesa", "kcb", "equity", "coop", "stima"]

    results = []
    for key in lender_keys:
        lender = KENYAN_LENDERS.get(key)
        if not lender:
            continue
        result, error = calculate_kenyan_loan(key, principal, months)
        if result:
            results.append(result)

    # Sort by total repayment (cheapest first)
    results.sort(key=lambda x: x["total_repayment"])
    return results


def format_comparison(results, principal, months):
    """Format lender comparison table."""
    if not results:
        return "No lenders to compare."

    lines = [f"📊 **Loan Comparison: KES {principal:,.0f} for {months} months**\n"]

    for i, r in enumerate(results):
        medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"**{i+1}.**"
        type_emoji = {"mobile": "📱", "bank": "🏦", "sacco": "🤝"}.get(r["type"], "💰")

        if r["rate_type"] == "Reducing Balance":
            rate_info = f"{r['annual_rate']}% p.a."
            monthly = f"KES {r['monthly_payment']:,.0f}/mo"
        else:
            rate_info = r.get("fee_rate", "")
            monthly = f"KES {r.get('monthly_payment', r['total_repayment']):,.0f}/mo"

        lines.append(f"{medal} {type_emoji} **{r['lender']}** — {rate_info}")
        lines.append(f"   Monthly: {monthly} | Total: KES {r['total_repayment']:,.0f}")

    cheapest = results[0]
    most_expensive = results[-1]
    savings = most_expensive["total_repayment"] - cheapest["total_repayment"]
    lines.append(f"\n💡 **Cheapest: {cheapest['lender']}** saves you **KES {savings:,.0f}** vs {most_expensive['lender']}")
    lines.append(f"\n_⚠️ Rates are approximate and may vary. Always confirm with the lender._")

    return "\n".join(lines)


# ══════════════════════════════════════════════
# EXPENSE PDF REPORT
# ══════════════════════════════════════════════

# Color palette for the report
_COLORS = {
    "primary":    (26, 115, 232),    # Blue
    "success":    (52, 168, 83),     # Green
    "warning":    (251, 188, 4),     # Amber
    "danger":     (234, 67, 53),     # Red
    "dark":       (32, 33, 36),      # Near-black
    "gray":       (95, 99, 104),     # Gray text
    "light_gray": (218, 220, 224),   # Borders
    "bg_light":   (248, 249, 250),   # Light background
    "white":      (255, 255, 255),
}

# Category colors for chart bars
_CAT_COLORS = [
    (26, 115, 232),   # Blue
    (234, 67, 53),    # Red
    (251, 188, 4),    # Amber
    (52, 168, 83),    # Green
    (156, 39, 176),   # Purple
    (255, 112, 67),   # Orange
    (0, 172, 193),    # Teal
    (121, 85, 72),    # Brown
    (96, 125, 139),   # Blue-gray
    (233, 30, 99),    # Pink
]


def generate_expense_pdf(user_name, monthly_data, budget_limit=None, income_data=None):
    """Generate a professional PDF expense report with visual charts. Returns bytes."""
    try:
        from fpdf import FPDF

        now = datetime.now(EAT_ZONE)
        month_name = now.strftime("%B %Y")

        if not monthly_data or not monthly_data.get("entries"):
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Helvetica", "", 12)
            pdf.cell(0, 10, "No expenses recorded this month.", ln=True)
            return pdf.output()

        total = monthly_data["total"]
        count = monthly_data["count"]
        categories = monthly_data.get("by_category", {})
        daily = monthly_data.get("by_day", {})
        entries = monthly_data.get("entries", [])
        sorted_cats = sorted(categories.items(), key=lambda x: -x[1])

        # Income data
        total_income = 0
        income_entries = []
        if income_data:
            total_income = income_data.get("total", 0)
            income_entries = income_data.get("entries", [])

        # Budget calculations — budget_limit should already be effective (base + income)
        remaining = (budget_limit - total) if budget_limit else None
        budget_pct = (total / budget_limit * 100) if budget_limit else 0
        day_of_month = now.day
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        days_left = max(days_in_month - day_of_month, 1)
        daily_avg = total / max(day_of_month, 1)
        daily_allowance = remaining / days_left if remaining and remaining > 0 else 0
        net_balance = total_income - total  # Income minus expenses

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=20)
        pw = 190  # Page width (usable)

        # ────────────────────────────────
        # PAGE 1: Cover + Summary
        # ────────────────────────────────
        pdf.add_page()

        # Header bar
        pdf.set_fill_color(*_COLORS["primary"])
        pdf.rect(0, 0, 210, 45, "F")
        pdf.set_text_color(*_COLORS["white"])
        pdf.set_font("Helvetica", "B", 22)
        pdf.set_y(10)
        pdf.cell(0, 10, f"Expense Report", ln=True, align="C")
        pdf.set_font("Helvetica", "", 13)
        pdf.cell(0, 8, f"{month_name}", ln=True, align="C")
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, f"Prepared for {user_name} by Emily AI  |  {now.strftime('%d %b %Y, %I:%M %p')}", ln=True, align="C")

        pdf.set_text_color(*_COLORS["dark"])
        pdf.ln(10)

        # ── Summary Cards ──
        if total_income > 0:
            # 4 cards if we have income data
            card_w = pw / 4
        else:
            card_w = pw / 3
        card_h = 22
        y_start = pdf.get_y()

        def _draw_card(x, y, label, value, color):
            pdf.set_fill_color(*_COLORS["bg_light"])
            pdf.set_draw_color(*_COLORS["light_gray"])
            pdf.rect(x, y, card_w - 2, card_h, "DF")
            pdf.set_xy(x + 2, y + 2)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*_COLORS["gray"])
            pdf.cell(card_w - 6, 5, label, ln=True)
            pdf.set_xy(x + 2, y + 8)
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(*color)
            pdf.cell(card_w - 6, 10, value)
            pdf.set_text_color(*_COLORS["dark"])

        col = 0
        if total_income > 0:
            _draw_card(10 + card_w * col, y_start, "INCOME", f"KES {total_income:,.0f}", _COLORS["success"])
            col += 1

        _draw_card(10 + card_w * col, y_start, "TOTAL SPENT", f"KES {total:,.0f}", _COLORS["danger"])
        col += 1
        _draw_card(10 + card_w * col, y_start, "TRANSACTIONS", f"{count}", _COLORS["primary"])
        col += 1

        if budget_limit:
            r_color = _COLORS["success"] if remaining and remaining > 0 else _COLORS["danger"]
            _draw_card(10 + card_w * col, y_start, "REMAINING", f"KES {remaining:,.0f}" if remaining else "N/A", r_color)
        else:
            _draw_card(10 + card_w * col, y_start, "DAILY AVG", f"KES {daily_avg:,.0f}", _COLORS["warning"])

        pdf.set_y(y_start + card_h + 5)

        # ── Net Balance (Income - Expenses) ──
        if total_income > 0:
            bal_color = _COLORS["success"] if net_balance >= 0 else _COLORS["danger"]
            bal_sign = "+" if net_balance >= 0 else ""
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*bal_color)
            pdf.cell(0, 5, f"Net Balance (Income - Expenses): KES {bal_sign}{net_balance:,.0f}", ln=True)
            pdf.set_text_color(*_COLORS["dark"])
            pdf.ln(3)

        # ── Budget Progress Bar ──
        if budget_limit:
            pdf.set_font("Helvetica", "B", 11)
            budget_label = "Budget Progress"
            if total_income > 0:
                base_limit = budget_limit - total_income
                budget_label += f" (Base: KES {base_limit:,.0f} + Income: KES {total_income:,.0f})"
            pdf.cell(0, 8, budget_label, ln=True)

            bar_w = pw
            bar_h = 12
            bar_y = pdf.get_y()

            # Background
            pdf.set_fill_color(*_COLORS["light_gray"])
            pdf.rect(10, bar_y, bar_w, bar_h, "F")

            # Filled portion
            fill_w = min(budget_pct / 100, 1.0) * bar_w
            if budget_pct > 90:
                pdf.set_fill_color(*_COLORS["danger"])
            elif budget_pct > 70:
                pdf.set_fill_color(*_COLORS["warning"])
            else:
                pdf.set_fill_color(*_COLORS["success"])
            if fill_w > 0:
                pdf.rect(10, bar_y, fill_w, bar_h, "F")

            # Label on bar
            pdf.set_xy(10, bar_y + 1)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*_COLORS["white"] if budget_pct > 15 else _COLORS["dark"])
            pdf.cell(bar_w, bar_h - 2, f"  KES {total:,.0f} of KES {budget_limit:,.0f} ({budget_pct:.0f}%)", align="L")
            pdf.set_text_color(*_COLORS["dark"])
            pdf.set_y(bar_y + bar_h + 3)

            # Daily allowance info
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*_COLORS["gray"])
            pdf.cell(0, 5, f"{days_left} days remaining  |  Daily allowance: KES {daily_allowance:,.0f}  |  Daily avg so far: KES {daily_avg:,.0f}", ln=True)
            pdf.set_text_color(*_COLORS["dark"])
            pdf.ln(5)

        # ────────────────────────────────
        # CATEGORY BREAKDOWN (horizontal bars)
        # ────────────────────────────────
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, "Spending by Category", ln=True)
        pdf.ln(2)

        if sorted_cats:
            max_amount = sorted_cats[0][1] if sorted_cats else 1
            bar_max_w = 100  # Max bar width in mm

            for idx, (cat, amount) in enumerate(sorted_cats):
                pct = (amount / total * 100) if total > 0 else 0
                bar_w = (amount / max_amount) * bar_max_w if max_amount > 0 else 0
                color = _CAT_COLORS[idx % len(_CAT_COLORS)]
                y = pdf.get_y()

                # Category name + percentage
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_xy(10, y)
                pdf.cell(40, 6, f"{cat.title()}", align="L")

                # Bar
                pdf.set_fill_color(*color)
                bar_x = 52
                if bar_w > 0:
                    pdf.rect(bar_x, y + 0.5, bar_w, 5, "F")

                # Amount label
                pdf.set_font("Helvetica", "", 8)
                pdf.set_xy(bar_x + bar_w + 2, y)
                pdf.cell(40, 6, f"KES {amount:,.0f} ({pct:.0f}%)")

                pdf.set_y(y + 8)

        pdf.ln(5)

        # ────────────────────────────────
        # DAILY SPENDING (vertical-ish bars)
        # ────────────────────────────────
        if daily:
            pdf.set_font("Helvetica", "B", 13)
            pdf.cell(0, 10, "Daily Spending", ln=True)
            pdf.ln(2)

            sorted_days = sorted(daily.items())
            max_day_amount = max(daily.values()) if daily else 1
            bar_max_w = 90

            for day_str, amount in sorted_days:
                y = pdf.get_y()
                bar_w = (amount / max_day_amount) * bar_max_w if max_day_amount > 0 else 0

                # Date
                pdf.set_font("Helvetica", "", 8)
                pdf.set_xy(10, y)
                # Shorten date: "2026-03-07" -> "Mar 07"
                try:
                    from datetime import datetime as _dt
                    short_date = _dt.strptime(day_str, "%Y-%m-%d").strftime("%b %d")
                except Exception:
                    short_date = day_str
                pdf.cell(22, 5, short_date, align="L")

                # Bar
                bar_x = 34
                # Color by amount relative to daily average
                if amount > daily_avg * 2:
                    pdf.set_fill_color(*_COLORS["danger"])
                elif amount > daily_avg * 1.3:
                    pdf.set_fill_color(*_COLORS["warning"])
                else:
                    pdf.set_fill_color(*_COLORS["success"])

                if bar_w > 0:
                    pdf.rect(bar_x, y + 0.5, bar_w, 4, "F")

                # Amount
                pdf.set_font("Helvetica", "", 8)
                pdf.set_xy(bar_x + bar_w + 2, y)
                pdf.cell(30, 5, f"KES {amount:,.0f}")

                pdf.set_y(y + 6.5)

        # ────────────────────────────────
        # PAGE 2+: CATEGORY DETAIL BREAKDOWN
        # ────────────────────────────────
        pdf.add_page()

        pdf.set_fill_color(*_COLORS["primary"])
        pdf.rect(0, 0, 210, 20, "F")
        pdf.set_text_color(*_COLORS["white"])
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_y(5)
        pdf.cell(0, 10, "Category Breakdown", ln=True, align="C")
        pdf.set_text_color(*_COLORS["dark"])
        pdf.ln(8)

        # Group entries by category
        cat_entries = {}
        for entry in entries:
            cat = entry.get("category", "general").title()
            if cat not in cat_entries:
                cat_entries[cat] = []
            cat_entries[cat].append(entry)

        for cat_idx, (cat, amount) in enumerate(sorted_cats):
            cat_title = cat.title()
            pct = (amount / total * 100) if total > 0 else 0
            color = _CAT_COLORS[cat_idx % len(_CAT_COLORS)]
            these_entries = cat_entries.get(cat_title, [])
            cat_count = len(these_entries)
            cat_avg = amount / cat_count if cat_count > 0 else 0

            # Check if we need a new page (need at least 60mm for header + a few rows)
            if pdf.get_y() > 230:
                pdf.add_page()

            y = pdf.get_y()

            # Category header bar
            pdf.set_fill_color(*color)
            pdf.rect(10, y, pw, 10, "F")
            pdf.set_text_color(*_COLORS["white"])
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_xy(12, y + 1)
            pdf.cell(80, 8, f"{cat_title}")
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_xy(130, y + 1)
            pdf.cell(70, 8, f"KES {amount:,.0f}  ({pct:.1f}%)", align="R")
            pdf.set_text_color(*_COLORS["dark"])
            pdf.set_y(y + 12)

            # Stats row
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*_COLORS["gray"])
            pdf.cell(60, 5, f"{cat_count} transactions  |  Avg: KES {cat_avg:,.0f}")

            # Find largest in this category
            if these_entries:
                largest = max(these_entries, key=lambda e: e.get("amount", 0))
                largest_desc = largest.get("description", "")[:25]
                largest_amt = largest.get("amount", 0)
                pdf.cell(0, 5, f"Largest: {largest_desc} (KES {largest_amt:,.0f})", align="R")
            pdf.ln()
            pdf.set_text_color(*_COLORS["dark"])

            # Transaction table for this category
            col_w = [25, 95, 40]  # Date, Description, Amount
            pdf.set_fill_color(*_COLORS["bg_light"])
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*_COLORS["gray"])
            pdf.cell(col_w[0], 5, "Date", fill=True)
            pdf.cell(col_w[1], 5, "Description", fill=True)
            pdf.cell(col_w[2], 5, "Amount (KES)", fill=True, align="R")
            pdf.ln()
            pdf.set_text_color(*_COLORS["dark"])

            # Sort entries by amount descending
            sorted_entries = sorted(these_entries, key=lambda e: -e.get("amount", 0))

            for row_idx, entry in enumerate(sorted_entries):
                if pdf.get_y() > 270:
                    pdf.add_page()

                date_str = entry.get("date_str", "")
                desc = entry.get("description", "")
                if len(desc) > 40:
                    desc = desc[:38] + ".."
                amt = entry.get("amount", 0)

                bg = _COLORS["white"] if row_idx % 2 else _COLORS["bg_light"]
                pdf.set_fill_color(*bg)
                pdf.set_font("Helvetica", "", 8)
                pdf.cell(col_w[0], 5, date_str, fill=True)
                pdf.cell(col_w[1], 5, desc, fill=True)

                # Bold the amount if it's large (> 2x category average)
                if amt > cat_avg * 2 and cat_count > 2:
                    pdf.set_font("Helvetica", "B", 8)
                    pdf.set_text_color(*_COLORS["danger"])
                else:
                    pdf.set_font("Helvetica", "", 8)
                pdf.cell(col_w[2], 5, f"{amt:,.0f}", fill=True, align="R")
                pdf.set_text_color(*_COLORS["dark"])
                pdf.ln()

            # Separator
            pdf.ln(4)
            pdf.set_draw_color(*_COLORS["light_gray"])
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(4)

        # ────────────────────────────────
        # INSIGHTS PAGE
        # ────────────────────────────────
        pdf.add_page()

        pdf.set_fill_color(*_COLORS["primary"])
        pdf.rect(0, 0, 210, 20, "F")
        pdf.set_text_color(*_COLORS["white"])
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_y(5)
        pdf.cell(0, 10, "Spending Insights", ln=True, align="C")
        pdf.set_text_color(*_COLORS["dark"])
        pdf.ln(8)

        # Top 5 biggest expenses
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Top 5 Biggest Expenses", ln=True)
        pdf.ln(2)

        top_entries = sorted(entries, key=lambda e: -e.get("amount", 0))[:5]
        for idx, entry in enumerate(top_entries):
            y = pdf.get_y()
            desc = entry.get("description", "")[:35]
            amt = entry.get("amount", 0)
            cat = entry.get("category", "general").title()
            date_str = entry.get("date_str", "")
            pct_of_total = (amt / total * 100) if total > 0 else 0

            # Rank number
            pdf.set_font("Helvetica", "B", 16)
            pdf.set_text_color(*_COLORS["primary"])
            pdf.set_xy(10, y)
            pdf.cell(12, 10, f"{idx + 1}")

            # Description + amount
            pdf.set_text_color(*_COLORS["dark"])
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_xy(22, y)
            pdf.cell(100, 5, desc)
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_xy(140, y)
            pdf.cell(60, 5, f"KES {amt:,.0f}", align="R")

            # Subtitle
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*_COLORS["gray"])
            pdf.set_xy(22, y + 5)
            pdf.cell(100, 5, f"{cat}  |  {date_str}  |  {pct_of_total:.1f}% of total")
            pdf.set_text_color(*_COLORS["dark"])
            pdf.set_y(y + 12)

        pdf.ln(5)

        # Recurring patterns
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Recurring Items", ln=True)
        pdf.ln(2)

        # Find descriptions that appear 3+ times
        desc_counts = {}
        desc_totals = {}
        for entry in entries:
            d = entry.get("description", "").lower().strip()
            # Normalize common variants
            for keyword in ["airtime", "electricity tokens", "transport", "weed", "water", "bundles"]:
                if keyword in d:
                    d = keyword
                    break
            desc_counts[d] = desc_counts.get(d, 0) + 1
            desc_totals[d] = desc_totals.get(d, 0) + entry.get("amount", 0)

        recurring = [(d, c, desc_totals[d]) for d, c in desc_counts.items() if c >= 3]
        recurring.sort(key=lambda x: -x[2])

        if recurring:
            col_w = [60, 30, 40, 40]
            pdf.set_fill_color(*_COLORS["dark"])
            pdf.set_text_color(*_COLORS["white"])
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(col_w[0], 6, "Item", fill=True)
            pdf.cell(col_w[1], 6, "Times", fill=True, align="C")
            pdf.cell(col_w[2], 6, "Total (KES)", fill=True, align="R")
            pdf.cell(col_w[3], 6, "Avg (KES)", fill=True, align="R")
            pdf.ln()
            pdf.set_text_color(*_COLORS["dark"])

            for idx, (desc, count, tot) in enumerate(recurring[:10]):
                bg = _COLORS["bg_light"] if idx % 2 == 0 else _COLORS["white"]
                pdf.set_fill_color(*bg)
                pdf.set_font("Helvetica", "", 8)
                pdf.cell(col_w[0], 6, desc.title()[:30], fill=True)
                pdf.cell(col_w[1], 6, f"{count}x", fill=True, align="C")
                pdf.set_font("Helvetica", "B", 8)
                pdf.cell(col_w[2], 6, f"{tot:,.0f}", fill=True, align="R")
                pdf.set_font("Helvetica", "", 8)
                pdf.cell(col_w[3], 6, f"{tot/count:,.0f}", fill=True, align="R")
                pdf.ln()
        else:
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(0, 6, "No frequently recurring expenses detected.", ln=True)

        pdf.ln(5)

        # People you send money to
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Money Sent to Others", ln=True)
        pdf.ln(2)

        people_totals = {}
        for entry in entries:
            desc = entry.get("description", "").lower()
            import re as _re2
            match = _re2.search(r'(?:sent?(?:\s+(?:to|money))?\s+(?:to\s+)?|helping|loaned?(?:\s+to)?)\s+(\w+)', desc)
            if match:
                name = match.group(1).title()
                if name.lower() not in ["money", "a", "the", "my", "to"]:
                    people_totals[name] = people_totals.get(name, 0) + entry.get("amount", 0)

        if people_totals:
            sorted_people = sorted(people_totals.items(), key=lambda x: -x[1])
            for name, total_sent in sorted_people:
                pdf.set_font("Helvetica", "", 9)
                pdf.cell(60, 6, f"{name}")
                pdf.set_font("Helvetica", "B", 9)
                pdf.cell(40, 6, f"KES {total_sent:,.0f}", align="R")
                pdf.ln()
        else:
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(0, 6, "No money transfers detected.", ln=True)

        # ────────────────────────────────
        # TRANSACTIONS TABLE
        # ────────────────────────────────
        pdf.add_page()

        pdf.set_fill_color(*_COLORS["primary"])
        pdf.rect(0, 0, 210, 20, "F")
        pdf.set_text_color(*_COLORS["white"])
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_y(5)
        pdf.cell(0, 10, "All Transactions", ln=True, align="C")
        pdf.set_text_color(*_COLORS["dark"])
        pdf.ln(8)

        # Table header
        col_widths = [25, 75, 35, 35, 20]  # Date, Description, Category, Amount, #
        headers = ["Date", "Description", "Category", "Amount (KES)", "#"]

        pdf.set_fill_color(*_COLORS["primary"])
        pdf.set_text_color(*_COLORS["white"])
        pdf.set_font("Helvetica", "B", 9)
        for i, (header, w) in enumerate(zip(headers, col_widths)):
            align = "R" if header == "Amount (KES)" else "L"
            pdf.cell(w, 7, header, border=0, fill=True, align=align)
        pdf.ln()

        pdf.set_text_color(*_COLORS["dark"])

        # Group entries by category for coloring
        for idx, entry in enumerate(entries):
            date_str = entry.get("date_str", "N/A")
            desc = entry.get("description", "N/A")
            if len(desc) > 32:
                desc = desc[:30] + ".."
            cat = entry.get("category", "general").title()
            amt = entry.get("amount", 0)

            # Alternate row colors
            if idx % 2 == 0:
                pdf.set_fill_color(*_COLORS["bg_light"])
            else:
                pdf.set_fill_color(*_COLORS["white"])

            pdf.set_font("Helvetica", "", 8)
            pdf.cell(col_widths[0], 6, date_str, border=0, fill=True)
            pdf.cell(col_widths[1], 6, desc, border=0, fill=True)

            # Category with colored dot
            pdf.set_font("Helvetica", "", 8)
            pdf.cell(col_widths[2], 6, cat, border=0, fill=True)

            pdf.set_font("Helvetica", "", 8)
            pdf.cell(col_widths[3], 6, f"{amt:,.0f}", border=0, fill=True, align="R")

            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*_COLORS["gray"])
            pdf.cell(col_widths[4], 6, f"{idx + 1}", border=0, fill=True, align="C")
            pdf.set_text_color(*_COLORS["dark"])
            pdf.ln()

            # Add separator line every row
            pdf.set_draw_color(*_COLORS["light_gray"])
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())

        # ── Category Totals Table ──
        pdf.ln(8)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Category Totals", ln=True)
        pdf.ln(2)

        # Header
        pdf.set_fill_color(*_COLORS["dark"])
        pdf.set_text_color(*_COLORS["white"])
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(60, 7, "Category", border=0, fill=True)
        pdf.cell(40, 7, "Amount", border=0, fill=True, align="R")
        pdf.cell(30, 7, "% of Total", border=0, fill=True, align="R")
        pdf.cell(40, 7, "Transactions", border=0, fill=True, align="R")
        pdf.ln()
        pdf.set_text_color(*_COLORS["dark"])

        # Count transactions per category
        cat_counts = {}
        for entry in entries:
            c = entry.get("category", "general").title()
            cat_counts[c] = cat_counts.get(c, 0) + 1

        for idx, (cat, amount) in enumerate(sorted_cats):
            pct = (amount / total * 100) if total > 0 else 0
            cat_title = cat.title()
            tx_count = cat_counts.get(cat_title, 0)

            if idx % 2 == 0:
                pdf.set_fill_color(*_COLORS["bg_light"])
            else:
                pdf.set_fill_color(*_COLORS["white"])

            pdf.set_font("Helvetica", "", 9)
            pdf.cell(60, 6, cat_title, border=0, fill=True)
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(40, 6, f"KES {amount:,.0f}", border=0, fill=True, align="R")
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(30, 6, f"{pct:.1f}%", border=0, fill=True, align="R")
            pdf.cell(40, 6, f"{tx_count}", border=0, fill=True, align="R")
            pdf.ln()

        # Total row
        pdf.set_fill_color(*_COLORS["primary"])
        pdf.set_text_color(*_COLORS["white"])
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(60, 7, "TOTAL", border=0, fill=True)
        pdf.cell(40, 7, f"KES {total:,.0f}", border=0, fill=True, align="R")
        pdf.cell(30, 7, "100%", border=0, fill=True, align="R")
        pdf.cell(40, 7, f"{count}", border=0, fill=True, align="R")
        pdf.ln()
        pdf.set_text_color(*_COLORS["dark"])

        # ── Footer ──
        pdf.ln(15)
        pdf.set_draw_color(*_COLORS["light_gray"])
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(3)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(*_COLORS["gray"])
        pdf.cell(0, 5, "Generated by Emily AI - Your Kenyan Financial Companion", ln=True, align="C")
        pdf.cell(0, 5, f"Report covers {month_name}  |  Data as of {now.strftime('%d %B %Y, %I:%M %p EAT')}", ln=True, align="C")

        return pdf.output()

    except Exception as e:
        logger.error(f"PDF generation error: {e}")
        return None


# ══════════════════════════════════════════════
# KENYAN PROVERBS & MOTIVATIONAL QUOTES
# ══════════════════════════════════════════════
KENYAN_PROVERBS = [
    ("Haraka haraka haina baraka.", "Hurrying has no blessings. Take your time, manze."),
    ("Mtaka yote kwa yote hukosa yote.", "He who wants everything loses everything. Focus on what matters."),
    ("Asiyefunzwa na mamaye hufunzwa na ulimwengu.", "If your mother doesn't teach you, the world will. Life is the best teacher."),
    ("Haba na haba hujaza kibaba.", "Little by little fills the pot. Small consistent steps, manze."),
    ("Dawa ya moto ni moto.", "The remedy for fire is fire. Fight fire with fire when you must."),
    ("Penye nia pana njia.", "Where there's a will, there's a way. Keep pushing, aki."),
    ("Samaki mkunje angali mbichi.", "Bend the fish while it's still fresh. Start early, don't wait."),
    ("Mgeni siku mbili, siku ya tatu mpe jembe.", "A guest for two days, on the third give them a hoe. Don't overstay your welcome."),
    ("Usipoziba ufa utajenga ukuta.", "If you don't fix the crack, you'll build a whole wall. Small problems become big ones."),
    ("Mwacha mila ni mtumwa.", "He who abandons culture is a slave. Know your roots."),
    ("Kiburi si maungano.", "Pride is not unity. Stay humble, stay together."),
    ("Pole pole ndio mwendo.", "Slowly is the way to go. Patience is a virtue."),
    ("Mnyonge mnyongeni haki yake mpeni.", "Even the weak deserve their rights. Justice for all."),
    ("Mtegemea cha nduguye hufa maskini.", "He who depends on his brother dies poor. Self-reliance matters."),
    ("Ukiona vyaelea, vimeundwa.", "What you see floating was built with effort. Success takes work."),
    ("Subira huvuta heri.", "Patience attracts blessings. Good things take time."),
    ("Kila ndege huruka na mbawa zake.", "Every bird flies with its own wings. Be yourself."),
    ("Asiye na bahati haachi kunena.", "The unlucky one never stops complaining. Change your mindset, change your life."),
    ("Mti hauendi ila kwa nyenzo.", "A tree doesn't move without a tool. Take action."),
    ("Akili ni mali.", "Wisdom is wealth. Invest in your mind, manze."),
    ("Umoja ni nguvu, utengano ni udhaifu.", "Unity is strength, division is weakness. Stick together."),
    ("Mwenye nguvu mpishe.", "Let the strong one pass. Know when to step aside."),
    ("Mchelea mwana kulia hulia yeye.", "He who fears his child crying will cry himself. Face tough conversations."),
    ("Jua la asubuhi haliishi mchana.", "The morning sun doesn't last all day. Enjoy the good times while they last."),
    ("Ataka cha mvunguni sharti ainame.", "If you want what's under the bed, you must bend. Success requires humility."),
    ("Mvumilivu hula mbivu.", "The patient one eats ripe fruit. Wait for the right moment."),
    ("Elimu haina mwisho.", "Education has no end. Keep learning, always."),
    ("Fimbo ya mbali haiuwi nyoka.", "A distant stick doesn't kill a snake. Act now, not later."),
    ("Maji yakimwagika hayazoleki.", "Spilled water cannot be collected. What's done is done — move forward."),
    ("Kuishi kwingi ni kuona mengi.", "To live long is to see much. Experience is the greatest teacher."),
]

MOTIVATIONAL_QUOTES = [
    "The best time to plant a tree was 20 years ago. The second best time is now.",
    "Your net worth is not your self-worth. But both can grow.",
    "Don't save what is left after spending; spend what is left after saving.",
    "Compound interest is the eighth wonder of the world. — Einstein",
    "The stock market is a device for transferring money from the impatient to the patient. — Buffett",
    "Risk comes from not knowing what you're doing. — Buffett",
    "The goal isn't more money. The goal is living life on your terms.",
    "Financial freedom is available to those who learn about it and work for it.",
    "It's not about how much you earn. It's about how much you keep.",
    "A budget is telling your money where to go instead of wondering where it went.",
]


def get_daily_quote():
    """Get a random Kenyan proverb or motivational quote."""
    if random.random() < 0.6:
        # 60% chance of Kenyan proverb
        swahili, english = random.choice(KENYAN_PROVERBS)
        return f"🇰🇪 **Proverb of the Day:**\n\n*\"{swahili}\"*\n\n{english}"
    else:
        # 40% chance of financial/motivational quote
        quote = random.choice(MOTIVATIONAL_QUOTES)
        return f"💡 **Daily Motivation:**\n\n*\"{quote}\"*\n\nNow go make it happen, manze! 💪"

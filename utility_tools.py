import os
import io
import random
import logging
import requests
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)

EAT_ZONE = pytz.timezone('Africa/Nairobi')


# ══════════════════════════════════════════════
# CURRENCY CONVERTER (live rates)
# ══════════════════════════════════════════════
CURRENCY_API_URL = "https://open.er-api.com/v6/latest/{base}"

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
    """Convert between currencies using live exchange rates."""
    try:
        from_c = CURRENCY_ALIASES.get(from_currency.upper(), from_currency.upper())
        to_c = CURRENCY_ALIASES.get(to_currency.upper(), to_currency.upper())

        response = requests.get(CURRENCY_API_URL.format(base=from_c), timeout=10)
        data = response.json()

        if data.get("result") != "success":
            return None, f"Couldn't fetch rates for {from_c}"

        rates = data.get("rates", {})
        if to_c not in rates:
            return None, f"Unknown currency: {to_c}"

        rate = rates[to_c]
        converted = amount * rate
        last_updated = data.get("time_last_update_utc", "unknown")

        result = {
            "amount": amount,
            "from": from_c,
            "to": to_c,
            "rate": rate,
            "converted": converted,
            "updated": last_updated,
        }
        return result, None

    except Exception as e:
        logger.error(f"Currency conversion error: {e}")
        return None, f"Currency conversion failed: {e}"


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
def generate_expense_pdf(user_name, monthly_data, budget_limit=None):
    """
    Generate a PDF expense report.
    Returns bytes of the PDF file.
    """
    try:
        from fpdf import FPDF

        now = datetime.now(EAT_ZONE)
        month_name = now.strftime("%B %Y")

        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)

        # Title
        pdf.set_font("Helvetica", "B", 20)
        pdf.cell(0, 15, f"Expense Report - {month_name}", ln=True, align="C")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 8, f"Generated by Emily AI for {user_name}", ln=True, align="C")
        pdf.cell(0, 8, f"Generated on: {now.strftime('%d %B %Y, %I:%M %p EAT')}", ln=True, align="C")
        pdf.ln(10)

        if not monthly_data or not monthly_data.get("entries"):
            pdf.set_font("Helvetica", "", 12)
            pdf.cell(0, 10, "No expenses recorded this month.", ln=True)
            return pdf.output()

        # Summary box
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, "Summary", ln=True)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 7, f"Total Spent: KES {monthly_data['total']:,.2f}", ln=True)
        pdf.cell(0, 7, f"Number of Transactions: {monthly_data['count']}", ln=True)
        if budget_limit:
            remaining = budget_limit - monthly_data['total']
            pdf.cell(0, 7, f"Monthly Budget: KES {budget_limit:,.2f}", ln=True)
            pdf.cell(0, 7, f"Remaining: KES {remaining:,.2f}", ln=True)
        pdf.ln(5)

        # Category breakdown
        if monthly_data.get("by_category"):
            pdf.set_font("Helvetica", "B", 14)
            pdf.cell(0, 10, "Spending by Category", ln=True)
            pdf.set_font("Helvetica", "", 11)
            sorted_cats = sorted(monthly_data["by_category"].items(), key=lambda x: -x[1])
            for cat, amount in sorted_cats:
                pct = (amount / monthly_data['total'] * 100) if monthly_data['total'] > 0 else 0
                pdf.cell(0, 7, f"  {cat.title()}: KES {amount:,.2f} ({pct:.0f}%)", ln=True)
            pdf.ln(5)

        # Daily breakdown
        if monthly_data.get("by_day"):
            pdf.set_font("Helvetica", "B", 14)
            pdf.cell(0, 10, "Daily Spending", ln=True)
            pdf.set_font("Helvetica", "", 11)
            for day, amount in sorted(monthly_data["by_day"].items()):
                pdf.cell(0, 7, f"  {day}: KES {amount:,.2f}", ln=True)
            pdf.ln(5)

        # Transaction table
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, "All Transactions", ln=True)

        # Table header
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(35, 8, "Date", border=1)
        pdf.cell(70, 8, "Description", border=1)
        pdf.cell(35, 8, "Category", border=1)
        pdf.cell(40, 8, "Amount (KES)", border=1, align="R")
        pdf.ln()

        # Table rows
        pdf.set_font("Helvetica", "", 9)
        for entry in monthly_data["entries"]:
            date_str = entry.get("date_str", "N/A")
            desc = entry.get("description", "N/A")[:30]
            cat = entry.get("category", "general").title()
            amt = entry.get("amount", 0)

            pdf.cell(35, 7, date_str, border=1)
            pdf.cell(70, 7, desc, border=1)
            pdf.cell(35, 7, cat, border=1)
            pdf.cell(40, 7, f"{amt:,.2f}", border=1, align="R")
            pdf.ln()

        # Footer
        pdf.ln(10)
        pdf.set_font("Helvetica", "I", 9)
        pdf.cell(0, 7, "This report was generated by Emily AI - Your Kenyan Financial Companion", ln=True, align="C")

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

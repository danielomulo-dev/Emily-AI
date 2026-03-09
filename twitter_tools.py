import os
import logging
import random
from datetime import datetime
from dotenv import load_dotenv
import pytz

load_dotenv()
logger = logging.getLogger(__name__)

EAT_ZONE = pytz.timezone('Africa/Nairobi')

# --- TWITTER CONFIG ---
TWITTER_API_KEY = os.getenv("TWITTER_API_KEY")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET")

_client = None


def is_configured():
    return bool(TWITTER_API_KEY and TWITTER_API_SECRET and TWITTER_ACCESS_TOKEN and TWITTER_ACCESS_SECRET)


def _get_client():
    """Get or create Twitter client."""
    global _client
    if _client:
        return _client

    # ── DEBUG: Verify env vars are loading correctly ──
    logger.info("=== TWITTER AUTH DEBUG ===")
    logger.info(f"  API Key:        {'✅ loaded (' + TWITTER_API_KEY[:6] + '...)' if TWITTER_API_KEY else '❌ MISSING'}")
    logger.info(f"  API Secret:     {'✅ loaded (' + TWITTER_API_SECRET[:6] + '...)' if TWITTER_API_SECRET else '❌ MISSING'}")
    logger.info(f"  Access Token:   {'✅ loaded (' + TWITTER_ACCESS_TOKEN[:6] + '...)' if TWITTER_ACCESS_TOKEN else '❌ MISSING'}")
    logger.info(f"  Access Secret:  {'✅ loaded (' + TWITTER_ACCESS_SECRET[:6] + '...)' if TWITTER_ACCESS_SECRET else '❌ MISSING'}")
    logger.info("=========================")

    if not is_configured():
        logger.warning("Twitter not configured — one or more env vars missing")
        return None

    try:
        import tweepy
        _client = tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_SECRET,
        )
        logger.info("Twitter client initialized successfully")
        return _client
    except Exception as e:
        logger.error(f"Twitter client error: {e}")
        return None


# ══════════════════════════════════════════════
# SEND TWEET
# ══════════════════════════════════════════════
def send_tweet(text):
    """Post a tweet. Max 280 characters."""
    client = _get_client()
    if not client:
        return False, "Twitter not configured"

    try:
        # Truncate to 280 chars
        if len(text) > 280:
            text = text[:277] + "..."

        response = client.create_tweet(text=text)
        tweet_id = response.data.get("id") if response.data else None
        logger.info(f"Tweet posted: {tweet_id}")
        return True, tweet_id

    except Exception as e:
        # ── DEBUG: Detailed error info for auth issues ──
        error_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            status = e.response.status_code
            error_msg = f"{status} {e.response.reason}\n{e.response.text}"
            logger.error(f"Tweet error [{status}]: {e.response.text}")
            if status == 401:
                logger.error(">>> 401 = Auth rejected. Check: (1) env vars match X console keys, "
                             "(2) keys were regenerated after enabling Read+Write, "
                             "(3) X account has $0 credits — may need to purchase credits")
            elif status == 403:
                logger.error(">>> 403 = Forbidden. Check: (1) app permissions set to Read+Write, "
                             "(2) billing/credits issue on X developer account")
        else:
            logger.error(f"Tweet error: {e}")
        return False, error_msg


def send_thread(tweets):
    """Post a thread of tweets."""
    client = _get_client()
    if not client:
        return False, "Twitter not configured"

    try:
        prev_id = None
        for text in tweets:
            if len(text) > 280:
                text = text[:277] + "..."

            if prev_id:
                response = client.create_tweet(text=text, in_reply_to_tweet_id=prev_id)
            else:
                response = client.create_tweet(text=text)

            prev_id = response.data.get("id") if response.data else None

        logger.info(f"Thread posted: {len(tweets)} tweets")
        return True, prev_id

    except Exception as e:
        logger.error(f"Thread error: {e}")
        return False, str(e)


# ══════════════════════════════════════════════
# CONTENT GENERATORS FOR AUTO-TWEETS
# ══════════════════════════════════════════════

# Kenyan proverbs for daily tweets
TWEET_PROVERBS = [
    "🇰🇪 \"Haraka haraka haina baraka.\"\n\nHurrying has no blessings. Take your time, do it right.\n\n#KenyanWisdom #EmilyAI",
    "🇰🇪 \"Haba na haba hujaza kibaba.\"\n\nLittle by little fills the pot. Small consistent steps win.\n\n#KenyanProverb #EmilyAI",
    "🇰🇪 \"Penye nia pana njia.\"\n\nWhere there's a will, there's a way. Keep pushing.\n\n#KenyanWisdom #EmilyAI",
    "🇰🇪 \"Akili ni mali.\"\n\nWisdom is wealth. Invest in your mind.\n\n#KenyanProverb #EmilyAI",
    "🇰🇪 \"Subira huvuta heri.\"\n\nPatience attracts blessings. Good things take time.\n\n#KenyanWisdom #EmilyAI",
    "🇰🇪 \"Ukiona vyaelea, vimeundwa.\"\n\nWhat you see floating was built with effort. Success takes work.\n\n#KenyanProverb #EmilyAI",
    "🇰🇪 \"Elimu haina mwisho.\"\n\nEducation has no end. Keep learning, always.\n\n#KenyanWisdom #EmilyAI",
    "🇰🇪 \"Usipoziba ufa utajenga ukuta.\"\n\nFix the crack now or build a whole wall later. Small problems become big ones.\n\n#KenyanProverb #EmilyAI",
    "🇰🇪 \"Mvumilivu hula mbivu.\"\n\nThe patient one eats ripe fruit. Wait for the right moment.\n\n#KenyanWisdom #EmilyAI",
    "🇰🇪 \"Fimbo ya mbali haiuwi nyoka.\"\n\nA distant stick doesn't kill a snake. Act now, not later.\n\n#KenyanProverb #EmilyAI",
    "🇰🇪 \"Mtegemea cha nduguye hufa maskini.\"\n\nDepending on others leads to poverty. Self-reliance matters.\n\n#KenyanWisdom #EmilyAI",
    "🇰🇪 \"Kila ndege huruka na mbawa zake.\"\n\nEvery bird flies with its own wings. Be yourself.\n\n#KenyanProverb #EmilyAI",
    "🇰🇪 \"Pole pole ndio mwendo.\"\n\nSlowly is the way to go. Patience wins.\n\n#KenyanWisdom #EmilyAI",
    "🇰🇪 \"Umoja ni nguvu, utengano ni udhaifu.\"\n\nUnity is strength, division is weakness.\n\n#KenyanProverb #EmilyAI",
]

FINANCE_TIPS = [
    "💰 Finance tip: Stop saving what's left after spending. Spend what's left after saving.\n\nAutomate it — set up a standing order on payday.\n\n#PersonalFinance #KenyanMoney #EmilyAI",
    "💰 Finance tip: M-Shwari charges 7.5% per month. That's 90% per year.\n\nA SACCO loan is ~12% per year. Know your options.\n\n#PersonalFinance #KenyanMoney #EmilyAI",
    "💰 Finance tip: Your savings account gives you 3% interest. Inflation is 6-8%.\n\nYou're losing money. Move to a money market fund (10-12%).\n\n#Investing #KenyanMoney #EmilyAI",
    "💰 Finance tip: Before investing in stocks, have 3-6 months of expenses saved.\n\nEmergency fund first. Always.\n\n#PersonalFinance #EmilyAI",
    "💰 Finance tip: Track every shilling for one month. Just one month.\n\nYou'll be shocked where your money actually goes.\n\n#BudgetTips #KenyanMoney #EmilyAI",
    "💰 Finance tip: The best time to start investing was 10 years ago. The second best time is today.\n\nEven KES 500/month in a money market fund compounds.\n\n#Investing #EmilyAI",
    "💰 Finance tip: Compound interest is the 8th wonder of the world.\n\nKES 5,000/month at 12% for 10 years = KES 1.15M\nSame amount for 20 years = KES 4.94M\n\n#CompoundInterest #EmilyAI",
    "💰 Finance tip: Before taking a loan, calculate the TOTAL cost.\n\nA KES 100K loan at 16% for 3 years costs you KES 135K total.\n\nThat extra 35K is real money.\n\n#DebtFree #EmilyAI",
    "💰 Finance tip: SACCO dividends in Kenya average 10-14% per year.\n\nThat's better than most bank products. If you qualify, join one.\n\n#SACCOs #KenyanMoney #EmilyAI",
    "💰 Finance tip: Don't invest money you'll need in the next 2 years.\n\nStocks go up AND down. Short-term money belongs in money market funds.\n\n#InvestingBasics #EmilyAI",
]


def get_daily_tweet():
    """Get a tweet for the daily morning post."""
    now = datetime.now(EAT_ZONE)
    day = now.weekday()

    # Rotate: Mon/Wed/Fri = proverb, Tue/Thu = finance, Sat/Sun = skip (movies handled separately)
    if day in (0, 2, 4):  # Mon, Wed, Fri
        return random.choice(TWEET_PROVERBS)
    elif day in (1, 3):  # Tue, Thu
        return random.choice(FINANCE_TIPS)
    else:
        return None  # Weekend — movie tweets handled by movie suggestion task


def format_movie_tweet(title, year, genre, imdb, rt, director):
    """Format a movie suggestion for Twitter."""
    tweet = f"🎬 Weekend Movie Pick: {title} ({year})\n\n"

    if director:
        tweet += f"Director: {director}\n"
    if genre:
        tweet += f"Genre: {genre}\n"
    if imdb:
        tweet += f"⭐ IMDB: {imdb}\n"
    if rt:
        tweet += f"🍅 RT: {rt}\n"

    tweet += "\n#MovieRecommendation #EmilyAI #FilmTwitter"

    # Ensure under 280 chars
    if len(tweet) > 280:
        tweet = tweet[:277] + "..."

    return tweet


def format_finance_tweet_from_tip(tip_text):
    """Format a Claude-generated finance tip for Twitter."""
    # Take first 200 chars of the tip, add hashtags
    clean = tip_text.strip()
    if len(clean) > 230:
        clean = clean[:227] + "..."

    tweet = f"💰 {clean}\n\n#PersonalFinance #EmilyAI"

    if len(tweet) > 280:
        tweet = tweet[:277] + "..."

    return tweet

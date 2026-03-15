import os
import re
import asyncio
import logging
import threading
import io
import random
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta
import pytz
import dateparser
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Discord & Gemini Imports
import discord
from discord.ext import commands, tasks
from google import genai
from google.genai import types

# Claude Import
import anthropic

# Tool Imports
from memory import get_user_profile, update_user_fact, set_voice_mode, add_message_to_history, get_chat_history
from image_tools import get_media_link
from web_tools import search_video_link, extract_text_from_url, get_latest_news, get_search_results, get_news_raw
from finance_tools import get_stock_price
from voice_tools import generate_voice_note, cleanup_voice_file
from tracker_tools import (
    log_expense, get_daily_spending, get_monthly_spending, set_budget_limit,
    get_budget_limit, format_budget_summary,
    log_income, get_monthly_income, get_effective_budget, format_full_budget_summary,
    delete_last_income, INCOME_CATEGORIES,
    recategorize_expenses,
    add_holding, remove_holding, get_portfolio, format_portfolio,
    add_reminder, get_due_reminders, mark_reminder_done, get_user_reminders,
    cancel_reminder,
    add_todo, complete_todo, remove_todo, get_todos, clear_done_todos, format_todos,
    get_server_settings, update_server_setting, set_news_channel, get_news_servers,
    set_server_persona, get_server_persona, PERSONA_PRESETS,
    set_alert_settings, get_alert_settings, get_all_alert_users,
    save_last_prices, get_last_prices, get_all_users_with_portfolios,
    set_voice_chat_channel, is_voice_chat_channel,
    save_sent_news, get_sent_news_urls,
)
from utility_tools import (
    convert_currency, format_currency_result,
    calculate_loan, calculate_mshwari, format_loan_result, format_mshwari_result,
    generate_expense_pdf, get_daily_quote,
    calculate_kenyan_loan, format_kenyan_loan, compare_lenders, format_comparison,
    KENYAN_LENDERS, LENDER_ALIASES,
)
from watchparty_tools import (
    add_to_watchlist, remove_from_watchlist, get_watchlist, vote_for_movie,
    mark_as_watched, get_watch_history, get_random_pick, get_top_voted,
    rate_movie, get_movie_ratings, get_group_top_rated,
    schedule_watchparty, join_watchparty, get_next_watchparty,
    get_due_watchparties, start_watchparty, end_watchparty,
    format_watchlist, format_ratings, format_top_rated,
    format_watch_history, format_watchparty,
    set_movie_channel, get_movie_suggestion_servers, log_movie_suggestion,
    get_past_suggestions, MOVIE_LANGUAGES, MOVIE_GENRES,
)
from trivia_tools import (
    get_trivia_question, format_trivia_question, start_game, get_game,
    record_answer, end_game, format_scores, EMOJI_OPTIONS, CATEGORY_NAMES,
)
from social_tools import (
    add_goal, get_active_goals, update_goal_progress, complete_goal, remove_goal,
    get_completed_goals, get_all_users_with_goals, format_goals,
    get_stale_goals, generate_accountability_message,
    update_saved_amount,
    add_anniversary, remove_anniversary, get_todays_events, get_upcoming_events,
    get_guilds_with_events, format_anniversaries,
    LEARNING_TOPICS,
)
from spotify_tools import (
    search_tracks, get_recommendations,
    format_search_results, format_recommendations,
    is_configured as spotify_configured, MOOD_PROFILES,
    save_user_artists, get_user_artists, get_all_weekly_music_users,
    get_recs_from_artists, format_weekly_recommendations,
)
from reddit_tools import (
    get_trending_posts, get_investment_buzz, get_stock_mentions, search_reddit,
    format_reddit_posts, format_investment_buzz, format_stock_mentions,
    is_configured as reddit_configured,
)
from error_monitor import (
    notify_owner, retry, async_retry, async_api_call_with_retry,
    handle_command_error, task_error_handler,
)
from twitter_tools import (
    send_tweet, send_thread, format_movie_tweet,
    is_configured as twitter_configured,
    is_film_tweet_day, get_film_tweet_time, get_film_tweet_prompt,
)
from messaging_tools import (
    send_sms, send_sms_batch, send_whatsapp,
    add_contact, remove_contact, remove_contact_by_name,
    get_contacts, format_contacts,
    is_configured as messaging_configured,
    get_reminder_log, log_reminder_sent,
)

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- INITIALIZE AI CLIENTS ---
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
claude_client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# --- MODELS ---
MODEL_GEMINI = os.getenv("MODEL_CHAT", "gemini-2.0-flash")
MODEL_CLAUDE = os.getenv("MODEL_CLAUDE", "claude-sonnet-4-5-20250929")

# --- CONFIG ---
MAX_HISTORY_MESSAGES = 30
API_TIMEOUT_SECONDS = 30
MAX_RETRIES = 2
MAX_FILE_SIZE_MB = 20

# --- PER-USER LOCKS ---
_user_locks = defaultdict(asyncio.Lock)

# --- MESSAGE DEDUP (prevent double replies) ---
_processed_messages = set()
MAX_DEDUP_SIZE = 500

# --- VOICE CONVERSATION MODE (per user) ---
_voice_mode_users = set()  # Users who want voice replies automatically

# --- EMILY'S STATUS ROTATION ---
EMILY_STATUSES = {
    "morning": [
        "☀️ Sipping chai in Nairobi",
        "📰 Reading the morning news",
        "💹 Checking the NSE opening",
    ],
    "afternoon": [
        "🍳 Thinking about lunch...",
        "📊 Analyzing market trends",
        "🎬 Planning tonight's movie",
    ],
    "evening": [
        "🍿 Movie time, manze!",
        "🌆 Nairobi sunsets hit different",
        "🎵 Vibing to Kenyan music",
    ],
    "night": [
        "🌙 Burning the midnight oil",
        "📚 Late night research mode",
        "😴 Even Emily needs rest... almost",
    ],
}

# --- TICKER MAP ---
NAME_TO_TICKER = {
    "SAFARICOM": "SCOM", "EQUITY": "EQTY", "KCB": "KCB",
    "COOPERATIVE": "COOP", "COOP": "COOP", "ABSA": "ABSA",
    "STANBIC": "SBIC", "NCBA": "NCBA", "DTB": "DTB",
    "DIAMOND TRUST": "DTB", "I&M": "IMH", "IM": "IMH",
    "HF": "HF", "CIC": "CIC", "BRITAM": "BRIT", "JUBILEE": "JUB",
    "LIBERTY": "LKN", "KENYA RE": "KNRE", "KENRE": "KNRE",
    "EABL": "EABL", "BAT": "BAT", "BAMBURI": "BAMB",
    "KENGEN": "KEGN", "KENYA POWER": "KPLC", "KPLC": "KPLC",
    "TOTAL": "TOTAL", "AIRTEL": "AIRTEL", "CENTUM": "CTUM",
    "SASINI": "SASN", "KAKUZI": "KUKZ", "NATION": "NMG",
    "NATION MEDIA": "NMG", "STANDARD GROUP": "SGL",
    "MICROSOFT": "MSFT", "APPLE": "AAPL", "GOOGLE": "GOOGL",
    "ALPHABET": "GOOGL", "TESLA": "TSLA", "AMAZON": "AMZN",
    "META": "META", "FACEBOOK": "META", "NVIDIA": "NVDA",
    "NETFLIX": "NFLX", "AMD": "AMD", "INTEL": "INTC",
    "BITCOIN": "BTC-USD", "ETHEREUM": "ETH-USD",
}

# --- FILE TYPES ---
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm", ".mkv", ".m4v", ".3gp"}
VIDEO_MIMES = {"video/mp4", "video/quicktime", "video/x-msvideo", "video/webm", "video/x-matroska", "video/3gpp"}
PDF_EXTENSIONS = {".pdf"}
PDF_MIMES = {"application/pdf"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
    ".csv", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".log",
    ".sh", ".bash", ".zsh", ".bat", ".ps1", ".sql", ".r", ".rb", ".go",
    ".java", ".kt", ".swift", ".c", ".cpp", ".h", ".hpp", ".rs", ".lua",
    ".php", ".pl", ".dart", ".scala", ".ex", ".exs", ".hs", ".elm",
    ".vue", ".jsx", ".tsx", ".svelte", ".env", ".gitignore", ".dockerfile",
}
TEXT_MIMES = {
    "text/plain", "text/markdown", "text/html", "text/css", "text/csv",
    "text/xml", "application/json", "application/xml", "application/javascript",
}
DOCUMENT_EXTENSIONS = {".doc", ".docx", ".xls", ".xlsx", ".pptx", ".odt", ".rtf"}

# --- PYDANTIC ---
class UserFact(BaseModel):
    fact: str = Field(description="The specific personal fact about the user.")
    category: str = Field(description="Type: preference, family, work, health, habit, finance, food, movies.")
    confidence: float = Field(description="Score between 0 and 1.")


# ══════════════════════════════════════════════
# EMILY'S PERSONA (shared across both models)
# ══════════════════════════════════════════════

# Mini persona for feature-specific prompts (roast, debate, learn, music, finance tips)
EMILY_MINI_PERSONA = (
    "You are Emily — a sharp, confident, funny Kenyan woman in her 30s from Nairobi. "
    "You're a financial analyst, cinephile, and foodie. You speak Kenyan English with "
    "natural Sheng slang (manze, aki, wueh, sawa, eish, kwani, fiti, pole). "
    "You're direct, opinionated, warm but honest. You reference Nairobi life — "
    "Java House, matatus, Naivas, Mombasa Road traffic. Never robotic or generic. "
    "NEVER start with 'Great question!' — just answer like a real person."
)

def _build_emily_prompt(current_time, facts_str):
    """Build Emily's persona prompt — used by both Gemini and Claude."""
    return f"""
You are Emily. A sharp, confident, opinionated Kenyan woman in her 30s based in Nairobi.
You are NOT a generic AI assistant. You are a real personality with deep expertise and strong takes.

CURRENT CONTEXT:
- Date & Time: {current_time}
- Location: Nairobi, Kenya.
- What you know about this person: {facts_str}

═══════════════════════════════════════
WHO YOU ARE — YOUR THREE PASSIONS:
═══════════════════════════════════════

🏦 FINANCE & INVESTMENTS (Your Day Job):
You are a financial analyst and broker who lives and breathes markets.
- You track the NSE (Nairobi Securities Exchange) daily. You know the blue chips (Safaricom, KCB, Equity, EABL, ABSA) inside out.
- You follow global markets too — S&P 500, NASDAQ, crypto, forex.
- You understand Kenyan retail investor culture: M-Shwari, money market funds, SACCOs, T-bills, government bonds.
- When advising on investments:
  * Always consider the person's risk appetite. Ask if you don't know it yet.
  * Give concrete opinions: "SCOM is undervalued right now" not "it depends on many factors."
  * Explain WHY — use P/E ratios, dividend yields, sector trends, earnings reports.
  * Know the Kenyan tax implications: withholding tax on dividends (15%), capital gains (5% on property).
  * Compare options: "Instead of putting 50K in a savings account at 3%, consider a money market fund at 10-12%."
  * Mention real Kenyan platforms: NSE app, AIB-AXYS, EFG Hermes, Faida Investment Bank, SIB.
- For global stocks, you have strong opinions on tech (NVDA, AAPL, MSFT), know about ETFs (VOO, QQQ), and follow crypto with healthy skepticism.
- You keep up with CBK monetary policy, interest rate decisions, KES/USD exchange rate movements.
- Use [STOCK: SYMBOL] tag when the user asks for live prices. NEVER make up prices.
- Add a disclaimer naturally: "but do your own research too" — don't make it robotic.

🍳 FOOD & COOKING (Your Weekend Passion):
You are a serious foodie with deep knowledge of Kenyan, East African, and global cuisine.
- Kenyan food is your foundation: nyama choma, ugali, sukuma wiki, pilau, chapati, githeri, mutura, irio, tilapia.
- You have OPINIONS: "Kenchic pilau is not real pilau, manze. Real pilau needs hours of slow-cooking with whole spices."
- You know the Nairobi food scene: Carnivore, Mama Oliech, Nyama Mama, About Thyme, Talisman, Burma Market street food.
- East African range: Ethiopian injera, Ugandan rolex, Tanzanian mishkaki, Zanzibari biryani.
- Global palate: Italian (proper carbonara vs cream nonsense), Japanese, Mexican, Indian, Thai, Middle Eastern.
- Cooking tips are practical and specific: "Add your onions to cold oil and cook LOW — that's the base of any good Kenyan stew."
- Strong opinions on food debates: "Ugali is better with hands, anyone using a fork has lost the plot."

🎬 CINEMA & FILM (Your Evening Escape):
You are a cinephile with encyclopedic knowledge and zero tolerance for bad takes.
- African cinema is close to your heart: Nairobi Half Life, Rafiki, The First Grader, Supa Modo, Atlantics.
- You follow Riverwood (Kenyan film industry) and Nollywood.
- Hollywood: Scorsese, Nolan, Villeneuve, Gerwig, Coogler, Peele. MCU peaked at Endgame.
- You recommend films based on mood with specific reasons.
- Hot takes: "Interstellar is Nolan's best, not The Dark Knight. Fight me."
- You understand cinematography, direction, screenwriting.
- You know streaming: Netflix, Prime Video, Showmax (East Africa), Apple TV+, Disney+.
- Rate films honestly. If a hyped movie is mid, say so.

═══════════════════════════════════════
HOW YOU TALK:
═══════════════════════════════════════
- Kenyan English with Sheng/slang: Sasa, Manze, Eish, Wueh, Pole, Aki, Basi, Sawa, Ati, Kwani, Maze, Niaje, Fiti, Poa, Mbaya, Noma, Si ni, Mbogi.
- Mix English and Swahili naturally — not every sentence, just where it flows.
- Direct and confident. State your opinion first, THEN explain.
- Warm but real. You'll critique, but also teach.
- Funny without trying too hard. Humor from honesty.
- Never robotic. Never "As an AI..." — you ARE Emily.
- NEVER start with "Great question!" or "That's a great point!" — that's AI talk. Just answer.

YOUR CONVERSATIONAL QUIRKS (use naturally, not all at once):
- When surprised: "Wueh!" or "Ati what?!" or "Kwani?!"
- When agreeing: "Si ni!" or "Sawa sawa" or "Fiti"
- When sympathizing: "Pole sana, manze" or "Eish, that's rough"
- When excited: "Aki!" or "Manze!" or "This one is noma!"
- When disapproving: "Mbaya" or "That's not it, manze"
- When thinking: "Hmm, sawa let me think about this..."
- Your favorite closing: "but that's just my take" or "do your thing though"
- You reference Nairobi life: traffic on Mombasa Road, Java House coffee, Naivas runs, matatu culture
- You compare things to Kenyan equivalents: "That's like buying Safaricom at 5 bob back in 2008"
- When someone does well: "You're moving like a boss!" or "Fiti sana!"
- When someone wastes money: "Manze, that money could've worked for you in a money market fund"

RESPONSE LENGTH:
- Casual greetings: 1-2 sentences max. "Sasa! What's good?" not a whole speech.
- Simple questions: 2-4 sentences. Get to the point.
- Advice/analysis: 2-4 paragraphs. Be thorough but not a lecture.
- Never pad responses with filler. If the answer is short, keep it short.

═══════════════════════════════════════
TOOL TAGS:
═══════════════════════════════════════
- Stock prices: [STOCK: SYMBOL] — NEVER invent prices, always use this tag for live data.
- Images: [IMG: search query] — ALWAYS use this tag when user asks for an image/picture/photo. Example: [IMG: Bruno Fernandez football]
- GIFs: [GIF: search query] — Use for animated GIFs. Example: [GIF: happy dance]
- Videos: [VIDEO: search query] — Use for YouTube videos. Example: [VIDEO: Inception trailer]
- CRITICAL: When someone asks for an image/picture/photo, you MUST include [IMG: descriptive query] in your response. Never just say "here's an image" without the tag.
- If user shares personal info, add [MEMORY SAVED] at the end.
- Do NOT include source URLs — they are appended automatically.

MEDIA HANDLING:
═══════════════════════════════════════
- When user sends an IMAGE: describe what you see, identify objects/people/scenes, give opinions.
- When user sends a VIDEO: analyze the content, describe what's happening, comment on key moments.
- When user sends a PDF: read and summarize, answer questions about the content.
- When user sends CODE: review it, find bugs, suggest improvements.
- For food photos: give honest, specific feedback on the dish.
- For financial documents/screenshots: analyze numbers and advise.

FILM OPINIONS — CRITICAL:
═══════════════════════════════════════
- When asked about a specific movie/show, ALWAYS search for it first. Read the plot, reviews, and ratings.
- Form your OWN opinion based on what you find. NEVER just reflect the user's rating back at them.
- If the user rates a movie 5/10, you might disagree — maybe you think it deserves a 7 or a 3. BE HONEST.
- Always reference specific details: plot points, acting, direction, cinematography, soundtrack.
- Compare it to similar films. Example: "This reminds me of X but doesn't hit as hard because..."
- You are a CINEPHILE. You have strong, independent opinions. You don't parrot or people-please.
- If you haven't seen/searched a film, SEARCH FOR IT. Never say "I can't access that."
"""


# ══════════════════════════════════════════════
# HIVE MIND: TASK ROUTER
# ══════════════════════════════════════════════
def _route_to_model(text, has_attachments=False, attachment_types=None):
    """
    Decide which model handles this task.
    Returns: "gemini" or "claude"
    
    ROUTING LOGIC:
    - Gemini: real-time search, current events, live data, image analysis, quick chat, voice
    - Claude: deep analysis, financial advice, code review, cooking tips, film discussion,
              document analysis, opinion/reasoning tasks, long-form responses
    """
    text_lower = text.lower() if text else ""
    attachment_types = attachment_types or []

    # ─── ALWAYS GEMINI (needs Google Search or native multimodal) ───
    
    # Current events / news / "what's happening"
    news_patterns = [
        r'(?:what|whats|what\'s)\s+(?:happening|going\s+on|the\s+latest|new|trending)',
        r'(?:latest|recent|current|today\'?s?)\s+(?:news|events|headlines|update)',
        r'(?:did\s+\w+\s+(?:win|lose|die|resign|announce))',
        r'(?:who\s+won|who\s+is\s+the\s+(?:current|new))',
        r'(?:is\s+it\s+(?:true|raining|going\s+to))',
        r'(?:weather|forecast|temperature)',
        r'(?:when\s+(?:is|does|did|will))',
        r'(?:score|results?\s+(?:of|for))',
        # Awards — match in any word order
        r'(?:oscar|grammy|emmy|golden\s+globe)',
        r'(?:nominat|nominee|winner|award).*(?:20\d{2})',
        # Factual lookups that need search
        r'(?:list|tell\s+me).*(?:nominat|winner|award|candidate)',
        r'(?:who\s+(?:is|are|was|were)\s+)',
        r'(?:what\s+(?:is|are|was|were)\s+the\s+)',
        r'(?:how\s+much\s+(?:is|does|did))',
        r'(?:where\s+(?:is|are|can\s+i))',
        r'(?:20(?:2[4-9]|3\d))',  # Any year 2024-2039 mentioned = probably needs search
    ]
    for pattern in news_patterns:
        if re.search(pattern, text_lower):
            return "gemini", "Real-time search needed"

    # Image/video analysis (Gemini has native vision + search)
    if "image" in attachment_types or "pdf" in attachment_types or "video" in attachment_types:
        # But if it's code review or document analysis, Claude is better
        analysis_words = ["review", "analyze", "analyse", "explain", "summarize", "summary",
                         "what's wrong", "fix", "improve", "feedback", "opinion", "critique"]
        if any(w in text_lower for w in analysis_words):
            return "claude", "Deep analysis of attachment"
        return "gemini", "Multimodal processing"

    # Quick greetings and small talk
    greeting_patterns = [
        r'^(?:hi|hey|hello|sasa|niaje|mambo|sup|yo|good\s+(?:morning|afternoon|evening))[\s!?.]*$',
        r'^(?:how\s+are\s+you|what\'?s?\s+up|habari)[\s!?.]*$',
    ]
    for pattern in greeting_patterns:
        if re.search(pattern, text_lower.strip()):
            return "gemini", "Quick greeting"

    # Live data lookups (prices, exchange rates, scores)
    live_data_patterns = [
        r'(?:price|rate|exchange|convert)\s+(?:of|for)',
        r'(?:usd|kes|eur|gbp)\s+(?:to|vs)',
        r'\$\w+',  # $TSLA style
    ]
    for pattern in live_data_patterns:
        if re.search(pattern, text_lower):
            return "gemini", "Live data lookup"

    # ─── ALWAYS CLAUDE (reasoning, analysis, advice) ───

    # Investment advice / financial analysis
    finance_patterns = [
        r'(?:should\s+i\s+(?:buy|sell|invest|hold))',
        r'(?:invest(?:ment)?\s+(?:advice|strategy|plan|portfolio|options?))',
        r'(?:where\s+(?:should|can)\s+i\s+(?:invest|put\s+my\s+money))',
        r'(?:risk\s+(?:appetite|tolerance|profile))',
        r'(?:dividend|p/?e\s+ratio|earnings|valuation|undervalued|overvalued)',
        r'(?:t-?bills?|bonds?|money\s+market|sacco|m-?shwari)',
        r'(?:portfolio|diversif|asset\s+allocation)',
        r'(?:compare|versus|vs)\s+.*(?:stock|fund|investment|etf)',
        r'(?:financial\s+(?:plan|goal|advice|freedom))',
        r'(?:budget|saving|retirement|pension)',
    ]
    for pattern in finance_patterns:
        if re.search(pattern, text_lower):
            return "claude", "Financial analysis/advice"

    # Code review / technical analysis
    code_patterns = [
        r'(?:review|check|fix|debug|improve|refactor)\s+(?:this|my|the)\s+(?:code|script|function|file)',
        r'(?:what\'?s?\s+wrong\s+with)',
        r'(?:how\s+(?:do|can|should)\s+i\s+(?:implement|build|create|code|write))',
        r'(?:explain\s+(?:this|the)\s+(?:code|function|error|bug))',
        r'```',  # Code block present
    ]
    for pattern in code_patterns:
        if re.search(pattern, text_lower):
            return "claude", "Code analysis"
    if "text_file" in attachment_types:
        return "claude", "Code/text file analysis"

    # Food / cooking (opinion-heavy → Claude)
    food_patterns = [
        r'(?:recipe|cook|cooking|ingredient|spice|dish|meal)',
        r'(?:how\s+(?:do|can|should)\s+i\s+(?:make|cook|prepare|bake))',
        r'(?:best\s+(?:restaurant|place\s+to\s+eat|food|dish))',
        r'(?:pilau|ugali|nyama\s+choma|chapati|biryani|samosa|mandazi)',
        r'(?:what\s+should\s+i\s+(?:eat|cook|make\s+for))',
        r'(?:food|taste|flavor|flavour|seasoning|marinade)',
    ]
    for pattern in food_patterns:
        if re.search(pattern, text_lower):
            return "claude", "Food/cooking expertise"

    # Film / cinema — split between Gemini (needs search for specific movies) and Claude (general opinions)
    # Specific movie/show lookup patterns → Gemini (needs to search for plot, reviews, ratings)
    film_search_patterns = [
        r'(?:review|rating|rated|rotten\s+tomatoes|imdb)',
        r'(?:what\s+do\s+you\s+think\s+(?:of|about)\s+)',
        r'(?:have\s+you\s+(?:seen|watched)\s+)',
        r'(?:your\s+(?:opinion|take|thoughts)\s+(?:on|about)\s+)',
        r'(?:rate\s+(?:the\s+)?(?:movie|film|show))',
        r'(?:how\s+(?:is|was)\s+(?:the\s+)?(?:movie|film|show))',
        r'(?:tell\s+me\s+about\s+(?:the\s+)?(?:movie|film|show))',
        r'(?:plot|summary|synopsis)',
        # Season, episode, character arc questions — need search for accuracy
        r'(?:season|series|episode|sn)\s*\d',
        r'(?:story\s*arc|character\s*arc|storyline|plot\s*line)',
        r'(?:what\s+happen|how\s+(?:does|did)\s+\w+\s+die|who\s+(?:kills?|dies|survives?))',
        r'(?:ending|finale|cliffhanger)',
        r'(?:cast|actor|actress)\s+(?:of|in|for)\s+',
        r'(?:spoiler|recap)',
        r'(?:animated\s+series|anime|cartoon)\s+.*(?:season|episode|arc)',
    ]
    for pattern in film_search_patterns:
        if re.search(pattern, text_lower):
            return "gemini", "Film lookup (needs search)"

    # General film discussion → Claude (opinions, recommendations)
    film_opinion_patterns = [
        r'(?:recommend\s+(?:a|me|some)\s+(?:movie|film|show|series))',
        r'(?:best\s+(?:movie|film|show|series|documentary))',
        r'(?:what\s+(?:should)\s+i\s+watch)',
        r'(?:director|actor|actress|screenplay|cinematograph)',
        r'(?:nollywood|riverwood|bollywood|hollywood|anime|k-?drama)',
        r'(?:movie|film|cinema)\s+(?:genre|type|like|similar)',
    ]
    for pattern in film_opinion_patterns:
        if re.search(pattern, text_lower):
            return "claude", "Film/cinema expertise"

    # Opinion / advice / analysis requests
    opinion_patterns = [
        r'(?:what\s+do\s+you\s+think)',
        r'(?:your\s+(?:opinion|take|thoughts|advice|recommendation))',
        r'(?:should\s+i)',
        r'(?:(?:help|advise|guide)\s+me)',
        r'(?:pros?\s+and\s+cons?)',
        r'(?:compare|comparison|difference\s+between)',
        r'(?:explain|analyze|analyse|break\s+down)',
        r'(?:teach\s+me|how\s+(?:does|do)\s+.*\s+work)',
    ]
    for pattern in opinion_patterns:
        if re.search(pattern, text_lower):
            return "claude", "Analysis/opinion request"

    # URL analysis (Claude is better at summarizing/analyzing fetched content)
    if re.search(r'https?://[^\s]+', text_lower):
        analysis_words = ["summarize", "summary", "analyze", "analyse", "read", "review",
                         "what does", "what is", "tell me about", "explain", "tldr", "tl;dr"]
        if any(w in text_lower for w in analysis_words):
            return "claude", "URL content analysis"

    # Long messages likely need deeper reasoning
    if len(text_lower) > 500:
        return "claude", "Long/complex query"

    # ─── DEFAULT: GEMINI (fast, has search, handles general chat) ───
    return "gemini", "General chat (default)"


# ══════════════════════════════════════════════
# RETRY WRAPPER (for Gemini)
# ══════════════════════════════════════════════
async def _call_gemini_with_retry(coro_func, *args, timeout=None, **kwargs):
    _timeout = timeout or API_TIMEOUT_SECONDS
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await asyncio.wait_for(
                coro_func(*args, **kwargs),
                timeout=_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(f"Gemini timed out (attempt {attempt}/{MAX_RETRIES})")
            last_error = TimeoutError("Gemini timed out")
        except Exception as e:
            logger.warning(f"Gemini error (attempt {attempt}/{MAX_RETRIES}): {e}")
            last_error = e
        if attempt < MAX_RETRIES:
            await asyncio.sleep(1.5 * attempt)
    raise last_error


async def _call_claude_with_retry(create_func, *args, timeout=None, **kwargs):
    """Call Claude API with retry logic."""
    _timeout = timeout or API_TIMEOUT_SECONDS
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await asyncio.wait_for(
                create_func(*args, **kwargs),
                timeout=_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(f"Claude timed out (attempt {attempt}/{MAX_RETRIES})")
            last_error = TimeoutError("Claude timed out")
        except Exception as e:
            logger.warning(f"Claude error (attempt {attempt}/{MAX_RETRIES}): {e}")
            last_error = e
        if attempt < MAX_RETRIES:
            await asyncio.sleep(1.5 * attempt)
    raise last_error


# ══════════════════════════════════════════════
# INJECTION PROTECTION
# ══════════════════════════════════════════════
def _sanitize_fact(fact):
    injection_patterns = [
        r'(?i)ignore\s+(all\s+)?(previous\s+)?instructions',
        r'(?i)you\s+are\s+now', r'(?i)system\s*:\s*',
        r'(?i)new\s+instructions?\s*:', r'(?i)override\s+prompt',
        r'(?i)disregard\s+(all\s+)?(prior\s+)?',
        r'(?i)forget\s+(all\s+)?(previous\s+)?',
        r'(?i)pretend\s+you\s+are', r'(?i)act\s+as\s+if',
    ]
    sanitized = fact
    for pattern in injection_patterns:
        sanitized = re.sub(pattern, '[REDACTED]', sanitized)
    return sanitized.replace('\n', ' ').strip()[:300]


# ══════════════════════════════════════════════
# HEALTH CHECK SERVER
# ══════════════════════════════════════════════
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if bot.is_ready():
            self.send_response(200)
        else:
            self.send_response(503)
        self.send_header('Content-type', 'text/plain')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(b"OK" if bot.is_ready() else b"Bot not ready")

    def do_HEAD(self):
        self.send_response(200 if bot.is_ready() else 503)
        self.send_header('Content-type', 'text/plain')
        self.send_header('Connection', 'close')
        self.end_headers()

    def log_message(self, format, *args):
        return

def run_health_server(ready_event):
    port = int(os.getenv("PORT", 8000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logger.info(f"Health check server LIVE on port {port}")
    ready_event.set()
    server.serve_forever()


# ══════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════
async def _process_all_tags(pattern, text, handler):
    matches = list(re.finditer(pattern, text, re.IGNORECASE))
    appendix = ""
    for m in matches:
        text = text.replace(m.group(0), "")
        try:
            search_term = m.group(1).strip()
            result = await asyncio.to_thread(handler, search_term)
            if result:
                appendix += f"\n\n{result}"
        except Exception as e:
            logger.error(f"Tag error for '{m.group(0)}': {e}")
    return text.strip(), appendix

async def send_chunked_reply(message, response):
    if not response:
        await message.reply("Manze, I got nothing. Try again?")
        return

    # Separate media URLs (GIFs/images) from text so Discord embeds them
    media_pattern = re.compile(
        r'\n*\s*(https?://\S+\.(?:gif|png|jpg|jpeg|webp)(?:\?\S*)?'
        r'|https?://(?:media\.giphy\.com|i\.giphy\.com|media\d?\.tenor\.com|c\.tenor\.com)/\S+)\s*\n*',
        re.IGNORECASE
    )
    media_urls = media_pattern.findall(response)
    # Deduplicate media URLs
    seen_urls = set()
    unique_media = []
    for url in media_urls:
        if url not in seen_urls:
            seen_urls.add(url)
            unique_media.append(url)
    media_urls = unique_media
    # Remove media URLs from the main text
    clean_response = media_pattern.sub('\n', response).strip()

    # Clean up orphaned "Here's an image/gif:" text that had its URL removed
    clean_response = re.sub(
        r'(?:Here\'?s?\s+(?:an?\s+)?(?:image|picture|photo|gif|it|him|her)\s*(?:of\s+\w+)?\s*:?\s*)\n*$',
        '', clean_response, flags=re.IGNORECASE
    ).strip()
    clean_response = re.sub(
        r'\n\s*(?:Here\s+you\s+go|Here\s+it\s+is)\s*:?\s*$',
        '', clean_response, flags=re.IGNORECASE
    ).strip()

    # Send main text
    if clean_response:
        chunks = []
        while len(clean_response) > 2000:
            split_at = clean_response.rfind('\n', 0, 2000)
            if split_at == -1:
                split_at = clean_response.rfind(' ', 0, 2000)
            if split_at == -1:
                split_at = 2000
            chunks.append(clean_response[:split_at])
            clean_response = clean_response[split_at:].lstrip()
        if clean_response:
            chunks.append(clean_response)

        for i, chunk in enumerate(chunks):
            if i == 0:
                await message.reply(chunk)
            else:
                await message.channel.send(chunk)

    # Send media URLs as separate messages so Discord auto-embeds them
    for url in media_urls[:3]:  # Limit to 3 media embeds
        await message.channel.send(url)


async def send_chunked_reply_channel(channel, text):
    """Send a long message to a channel (not as a reply), splitting into chunks."""
    if not text:
        return
    chunks = []
    while len(text) > 2000:
        split_at = text.rfind('\n', 0, 2000)
        if split_at == -1:
            split_at = text.rfind(' ', 0, 2000)
        if split_at == -1:
            split_at = 2000
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    if text:
        chunks.append(text)
    for chunk in chunks:
        await channel.send(chunk)


def _extract_sources(response):
    try:
        if not response.candidates:
            return ""
        candidate = response.candidates[0]
        grounding_metadata = getattr(candidate, 'grounding_metadata', None)
        if not grounding_metadata:
            return ""
        grounding_chunks = getattr(grounding_metadata, 'grounding_chunks', None)
        if not grounding_chunks:
            return ""
        sources = []
        seen = set()
        for chunk in grounding_chunks:
            web = getattr(chunk, 'web', None)
            if web:
                uri = getattr(web, 'uri', None)
                title = getattr(web, 'title', None)
                if uri and uri not in seen and "vertexaisearch" not in uri:
                    seen.add(uri)
                    sources.append(f"• {title or 'Link'}: {uri}")
        if not sources:
            return ""
        return "\n\n**Sources:**\n" + "\n".join(sources[:5])
    except Exception as e:
        logger.error(f"Source extraction error: {e}")
        return ""


# ══════════════════════════════════════════════
# ATTACHMENT HANDLING
# ══════════════════════════════════════════════
def _get_file_extension(filename):
    return os.path.splitext(filename.lower())[1]

def _is_audio_attachment(att):
    audio_types = ["audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/webm"]
    if att.content_type and any(t in att.content_type for t in audio_types):
        return True
    if _get_file_extension(att.filename) in {".ogg", ".mp3", ".m4a", ".wav", ".webm", ".opus"}:
        return True
    return hasattr(att, 'is_voice_message') and att.is_voice_message

def _is_image_attachment(att):
    if att.content_type and any(t in att.content_type for t in IMAGE_MIMES):
        return True
    return _get_file_extension(att.filename) in IMAGE_EXTENSIONS

def _is_video_attachment(att):
    if att.content_type and any(t in att.content_type for t in VIDEO_MIMES):
        return True
    return _get_file_extension(att.filename) in VIDEO_EXTENSIONS

def _is_pdf_attachment(att):
    if att.content_type and any(t in att.content_type for t in PDF_MIMES):
        return True
    return _get_file_extension(att.filename) in PDF_EXTENSIONS

def _is_text_attachment(att):
    if att.content_type:
        base = att.content_type.split(";")[0].strip()
        if base in TEXT_MIMES:
            return True
    return _get_file_extension(att.filename) in TEXT_EXTENSIONS

def _is_document_attachment(att):
    return _get_file_extension(att.filename) in DOCUMENT_EXTENSIONS

async def download_attachment(att):
    try:
        return await att.read()
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return None

async def process_image(att):
    if att.size > MAX_FILE_SIZE_MB * 1024 * 1024:
        return None, f"Image too large ({att.size // (1024*1024)}MB)."
    data = await download_attachment(att)
    if not data:
        return None, "Couldn't download that image."
    mime = (att.content_type or "image/png").split(";")[0].strip()
    return {"inline_data": {"data": data, "mime_type": mime}}, None

async def process_video(att):
    """Download video and return as Gemini-ready inline_data part."""
    size_mb = att.size / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        return None, f"Video too large ({size_mb:.1f}MB). Max is {MAX_FILE_SIZE_MB}MB — try a shorter clip!"
    data = await download_attachment(att)
    if not data:
        return None, "Couldn't download that video."
    mime = (att.content_type or "video/mp4").split(";")[0].strip()
    # Normalize mime types Gemini accepts
    mime_map = {
        "video/x-msvideo": "video/mp4",
        "video/x-matroska": "video/mp4",
        "video/3gpp": "video/mp4",
    }
    mime = mime_map.get(mime, mime)
    logger.info(f"Video processed: {att.filename} ({size_mb:.1f}MB, {mime})")
    return {"inline_data": {"data": data, "mime_type": mime}}, None

async def process_pdf(att):
    if att.size > MAX_FILE_SIZE_MB * 1024 * 1024:
        return None, f"PDF too large ({att.size // (1024*1024)}MB)."
    data = await download_attachment(att)
    if not data:
        return None, "Couldn't download that PDF."
    return {"inline_data": {"data": data, "mime_type": "application/pdf"}}, None

async def process_text_file(att):
    if att.size > 1 * 1024 * 1024:
        return None, "Text file too large (over 1MB)."
    data = await download_attachment(att)
    if not data:
        return None, "Couldn't download that file."
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
        except Exception:
            return None, "Encoding not supported."
    ext = _get_file_extension(att.filename)
    lang = ext.lstrip(".") if ext else "text"
    if len(text) > 15000:
        text = text[:15000] + "\n\n... (truncated)"
    return {"text": f"**File: {att.filename}**\n```{lang}\n{text}\n```"}, None

async def process_document(att):
    ext = _get_file_extension(att.filename)
    return {"text": f"[User uploaded: {att.filename}]"}, (
        f"I can see you sent a `{ext}` file. Save it as a PDF and I can read it!"
    )

async def process_attachments(message):
    parts = []
    audio_bytes = None
    audio_mime = None
    warnings = []
    attachment_types = []

    # Check if the MESSAGE itself is flagged as a voice message
    is_voice_message = bool(message.flags.value & (1 << 13))

    for att in message.attachments:
        # Check specific types FIRST — images, PDFs, text files take priority
        # This prevents the voice message flag from capturing non-audio attachments
        if _is_image_attachment(att):
            part, err = await process_image(att)
            if part:
                parts.append(part)
                attachment_types.append("image")
                logger.info(f"Image processed: {att.filename}")
            if err:
                warnings.append(err)

        elif _is_video_attachment(att):
            part, err = await process_video(att)
            if part:
                parts.append(part)
                attachment_types.append("video")
            if err:
                warnings.append(err)

        elif _is_pdf_attachment(att):
            part, err = await process_pdf(att)
            if part:
                parts.append(part)
                attachment_types.append("pdf")
                logger.info(f"PDF processed: {att.filename}")
            if err:
                warnings.append(err)

        elif _is_text_attachment(att):
            part, err = await process_text_file(att)
            if part:
                parts.append(part)
                attachment_types.append("text_file")
                logger.info(f"Text file processed: {att.filename}")
            if err:
                warnings.append(err)

        elif _is_document_attachment(att):
            part, err = await process_document(att)
            if part:
                parts.append(part)
            if err:
                warnings.append(err)

        elif _is_audio_attachment(att) or \
             (is_voice_message and hasattr(att, 'is_voice_message') and att.is_voice_message):
            # Only treat as audio if it's actually an audio file or a confirmed voice message
            data = await download_attachment(att)
            if data:
                audio_bytes = data
                audio_mime = (att.content_type or "audio/ogg").split(";")[0].strip()
                attachment_types.append("audio")
                logger.info(f"Audio processed: {att.filename}")
            else:
                warnings.append("Couldn't download that voice note.")

        elif is_voice_message:
            # Last resort: message is flagged as voice but attachment type is unknown
            data = await download_attachment(att)
            if data:
                audio_bytes = data
                audio_mime = (att.content_type or "audio/ogg").split(";")[0].strip()
                attachment_types.append("audio")
                logger.info(f"Voice message (flag): {att.filename}")
            else:
                warnings.append("Couldn't download that voice note.")

        else:
            warnings.append(f"Can't process `{att.filename}` — try PDF, image, or text!")

    return parts, audio_bytes, audio_mime, warnings, attachment_types


# ══════════════════════════════════════════════
# VOICE
# ══════════════════════════════════════════════
async def transcribe_audio_with_gemini(audio_bytes, mime_type="audio/ogg"):
    try:
        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
        response = await _call_gemini_with_retry(
            gemini_client.aio.models.generate_content,
            model=MODEL_GEMINI,
            contents=[types.Content(role="user", parts=[
                audio_part,
                types.Part.from_text(text="Transcribe this audio exactly as spoken. Return ONLY the text."),
            ])],
            timeout=15,
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return None

async def send_voice_reply(message, text_response):
    try:
        filename = f"reply_{message.id}.mp3"
        voice_file = await generate_voice_note(text_response, filename=filename)
        if voice_file and os.path.exists(voice_file):
            await message.reply(file=discord.File(voice_file, filename="emily_reply.mp3"))
            cleanup_voice_file(voice_file)
            return True
        return False
    except Exception as e:
        logger.error(f"Voice reply failed: {e}")
        cleanup_voice_file(f"reply_{message.id}.mp3")
        return False


# ══════════════════════════════════════════════
# URL EXTRACTION & CONTENT FETCHING
# ══════════════════════════════════════════════
URL_PATTERN = re.compile(r'https?://[^\s<>"\')\]]+')

async def extract_and_fetch_urls(text):
    """
    Find URLs in message text, fetch their content, and return as text parts.
    Returns: (list of text part dicts, list of extracted URLs)
    """
    urls = URL_PATTERN.findall(text)
    if not urls:
        return [], []

    parts = []
    fetched_urls = []

    # Limit to 3 URLs max to avoid slowdowns
    for url in urls[:3]:
        try:
            logger.info(f"Fetching URL: {url}")
            content = await asyncio.to_thread(extract_text_from_url, url)
            if content and len(content.strip()) > 50:
                parts.append({"text": f"[Content from {url}]:\n{content[:5000]}"})
                fetched_urls.append(url)
                logger.info(f"URL fetched: {url} ({len(content)} chars)")
            else:
                logger.warning(f"URL returned minimal content: {url}")
        except Exception as e:
            logger.error(f"Failed to fetch URL {url}: {e}")

    return parts, fetched_urls


# ══════════════════════════════════════════════
# STOCK DETECTOR
# ══════════════════════════════════════════════
def _detect_stock_query(text):
    patterns = [
        r'(?:current\s+)?(?:price|stock|shares?|value)\s+(?:of\s+|for\s+)?["\']?(\w[\w\s&]*\w?)["\']?',
        r'["\']?(\w[\w\s&]*\w?)["\']?\s+(?:stock|shares?|price|current price)',
        r'how\s+(?:is|are|did|has|much)\s+["\']?(\w[\w\s&]*\w?)["\']?\s+(?:stock|shares?|perform|doing|trading|priced)',
        r'how\s+(?:is|are|did|has)\s+["\']?(\w[\w\s&]*\w?)["\']?\s+(?:on\s+(?:the\s+)?(?:nse|market|exchange))',
        r'(?:tell\s+me\s+about|check|get|fetch|look\s+up)\s+["\']?(\w[\w\s&]*\w?)["\']?\s+(?:stock|shares?|price)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw = match.group(1).strip().upper()
            if raw in NAME_TO_TICKER:
                return NAME_TO_TICKER[raw]
            if len(raw) <= 6 and raw.isalpha():
                return raw
            for word in raw.split():
                if word in NAME_TO_TICKER:
                    return NAME_TO_TICKER[word]
    dollar_match = re.search(r'\$(\w{1,6})', text.upper())
    if dollar_match:
        ticker = dollar_match.group(1)
        return NAME_TO_TICKER.get(ticker, ticker)
    return None


# ══════════════════════════════════════════════
# GEMINI BRAIN
# ══════════════════════════════════════════════
async def _get_gemini_response(conversation_history, emily_prompt):
    """Get response from Gemini (has Google Search)."""
    trimmed = conversation_history[-MAX_HISTORY_MESSAGES:]

    formatted_contents = []
    for msg in trimmed:
        parts = []
        for p in msg.get("parts", []):
            if isinstance(p, str):
                parts.append(types.Part.from_text(text=p))
            elif isinstance(p, dict):
                if "text" in p:
                    parts.append(types.Part.from_text(text=p["text"]))
                elif "inline_data" in p:
                    parts.append(types.Part.from_bytes(
                        data=p["inline_data"]["data"],
                        mime_type=p["inline_data"]["mime_type"]
                    ))
        if parts:
            formatted_contents.append(types.Content(role=msg["role"], parts=parts))

    search_tool = types.Tool(google_search=types.GoogleSearch())
    response = await _call_gemini_with_retry(
        gemini_client.aio.models.generate_content,
        model=MODEL_GEMINI,
        contents=formatted_contents,
        config=types.GenerateContentConfig(
            tools=[search_tool],
            system_instruction=emily_prompt,
            response_modalities=["TEXT"],
        )
    )

    return response.text or "", _extract_sources(response)


# ══════════════════════════════════════════════
# CLAUDE BRAIN
# ══════════════════════════════════════════════
async def _get_claude_response(conversation_history, emily_prompt):
    """Get response from Claude (better reasoning, no search)."""
    trimmed = conversation_history[-MAX_HISTORY_MESSAGES:]

    # Convert to Claude's message format
    claude_messages = []
    for msg in trimmed:
        role = "user" if msg["role"] == "user" else "assistant"
        content_blocks = []

        for p in msg.get("parts", []):
            if isinstance(p, str):
                content_blocks.append({"type": "text", "text": p})
            elif isinstance(p, dict):
                if "text" in p:
                    content_blocks.append({"type": "text", "text": p["text"]})
                elif "inline_data" in p:
                    import base64
                    mime = p["inline_data"]["mime_type"]
                    data = p["inline_data"]["data"]

                    # Claude supports images natively
                    if mime.startswith("image/"):
                        b64 = base64.b64encode(data).decode("utf-8")
                        content_blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime,
                                "data": b64,
                            }
                        })
                    elif mime == "application/pdf":
                        b64 = base64.b64encode(data).decode("utf-8")
                        content_blocks.append({
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": mime,
                                "data": b64,
                            }
                        })
                    else:
                        content_blocks.append({"type": "text", "text": f"[Unsupported attachment: {mime}]"})

        if content_blocks:
            # Claude requires alternating user/assistant messages
            # Merge consecutive same-role messages
            if claude_messages and claude_messages[-1]["role"] == role:
                claude_messages[-1]["content"].extend(content_blocks)
            else:
                claude_messages.append({"role": role, "content": content_blocks})

    # Ensure conversation starts with user message (Claude requirement)
    if claude_messages and claude_messages[0]["role"] == "assistant":
        claude_messages.insert(0, {"role": "user", "content": [{"type": "text", "text": "Hi"}]})

    # Ensure conversation doesn't end with assistant (we want a new response)
    if claude_messages and claude_messages[-1]["role"] == "assistant":
        claude_messages.append({"role": "user", "content": [{"type": "text", "text": "Continue."}]})

    if not claude_messages:
        claude_messages = [{"role": "user", "content": [{"type": "text", "text": "Hi Emily!"}]}]

    try:
        response = await asyncio.wait_for(
            claude_client.messages.create(
                model=MODEL_CLAUDE,
                max_tokens=2048,
                system=emily_prompt,
                messages=claude_messages,
            ),
            timeout=API_TIMEOUT_SECONDS,
        )

        # Extract text from Claude's response
        text = ""
        for block in response.content:
            if block.type == "text":
                text += block.text

        return text, ""  # Claude has no grounding sources

    except Exception as e:
        logger.error(f"Claude failed: {e}")
        raise


# ══════════════════════════════════════════════
# EMILY'S BRAIN (HIVE MIND ORCHESTRATOR)
# ══════════════════════════════════════════════
async def get_ai_response(conversation_history, user_id, chosen_model, route_reason, guild_id=None):
    """
    Routes to the right model, handles memory, tags, and fallback.
    Returns tuple: (response_text, source_links)
    """
    try:
        eat_zone = pytz.timezone('Africa/Nairobi')
        current_time = datetime.now(eat_zone).strftime("%A, %d %B %Y, %I:%M %p EAT")

        profile = get_user_profile(user_id)
        safe_facts = [_sanitize_fact(f) for f in profile.get("facts", [])]
        facts_str = "\n- ".join(safe_facts) if safe_facts else "A new friend — haven't learned much about them yet."

        emily_prompt = _build_emily_prompt(current_time, facts_str)

        # ─── CUSTOM SERVER PERSONA ───
        if guild_id:
            custom_persona = get_server_persona(str(guild_id))
            if custom_persona:
                emily_prompt += f"""

═══════════════════════════════════════
SERVER PERSONALITY MODIFIER:
═══════════════════════════════════════
{custom_persona}
Adapt your responses to match this style while keeping your core Emily identity.
"""

        # Add model-specific instructions
        if chosen_model == "gemini":
            emily_prompt += """
ABSOLUTE SEARCH RULES — VIOLATION IS UNACCEPTABLE:
- You MUST use Google Search for ANY factual question: awards, events, people, dates, news, scores.
- You MUST use Google Search for ANY question about specific TV show/movie plots, character arcs, season details, or episode events.
- If a message contains [IMPORTANT: You MUST use Google Search], you MUST search. No exceptions.
- NEVER list nominees, winners, facts, stats, or current events without searching first.
- NEVER describe a character's fate, death, or story arc without searching first — your memory may be WRONG.
- If you answer a factual question without searching, you are WRONG. Always search.
- If search returns no results, say "I couldn't verify that right now" — NEVER guess or fabricate.
- When you search, cite what you found. Be specific with names, dates, and details from search results.
"""
        else:
            emily_prompt += """
IMPORTANT:
- You do NOT have access to Google Search or live data.
- For factual claims about TV show plots, character arcs, specific episode events, or who dies/lives — 
  be honest if you're not 100% sure. Say "from what I remember" or "I'd need to double-check that."
  Do NOT present uncertain plot details as facts.
- For factual claims, be clear about what you know vs what might have changed.
- If the user needs LIVE data (stock prices, news, weather), tell them to ask again 
  and you'll route to your search brain. Or use [STOCK: SYMBOL] for prices.
- Your strength is ANALYSIS, ADVICE, and OPINIONS. Lean into that.
"""

        logger.info(f"🧠 Hive Mind → {chosen_model.upper()} | Reason: {route_reason}")

        # ─── FORCE SEARCH: Inject search instruction for factual queries ───
        # Gemini sometimes ignores the system prompt and answers from memory.
        # This prepends a direct instruction to the user's message forcing it to search.
        if chosen_model == "gemini" and route_reason == "Real-time search needed":
            if conversation_history:
                last_msg = conversation_history[-1]
                if last_msg.get("role") == "user" and last_msg.get("parts"):
                    first_part = last_msg["parts"][0]
                    original_text = first_part if isinstance(first_part, str) else first_part.get("text", "")
                    forced_text = (
                        f"[IMPORTANT: You MUST use Google Search to answer this. "
                        f"Do NOT answer from memory. Search first, then respond.]\n\n"
                        f"{original_text}"
                    )
                    # Modify a copy, not the original
                    conversation_history = [m for m in conversation_history]
                    conversation_history[-1] = {
                        "role": "user",
                        "parts": [{"text": forced_text}] + last_msg["parts"][1:]
                    }

        # ─── TRY PRIMARY MODEL ───
        final_text = ""
        source_links = ""
        try:
            if chosen_model == "gemini":
                final_text, source_links = await _get_gemini_response(conversation_history, emily_prompt)
            else:
                final_text, source_links = await _get_claude_response(conversation_history, emily_prompt)

            # ─── FALLBACK: If primary returned empty text, try the other model ───
            if not final_text or not final_text.strip():
                fallback = "claude" if chosen_model == "gemini" else "gemini"
                logger.warning(f"{chosen_model.upper()} returned empty response, falling back to {fallback.upper()}")
                try:
                    if fallback == "gemini":
                        final_text, source_links = await _get_gemini_response(conversation_history, emily_prompt)
                    else:
                        final_text, source_links = await _get_claude_response(conversation_history, emily_prompt)
                except Exception as fallback_error:
                    logger.error(f"Fallback {fallback} also failed: {fallback_error}")

        except Exception as primary_error:
            # ─── FALLBACK TO OTHER MODEL (on exception) ───
            fallback = "claude" if chosen_model == "gemini" else "gemini"
            logger.warning(f"{chosen_model.upper()} failed ({primary_error}), falling back to {fallback.upper()}")
            try:
                if fallback == "gemini":
                    final_text, source_links = await _get_gemini_response(conversation_history, emily_prompt)
                else:
                    final_text, source_links = await _get_claude_response(conversation_history, emily_prompt)
            except Exception as fallback_error:
                logger.error(f"Both models failed. Primary: {primary_error}, Fallback: {fallback_error}")
                return "Manze, both my brains are jammed right now. Try again in a sec?", ""

        # ─── GUARD: Ensure final_text is never None ───
        if not final_text or not final_text.strip():
            logger.error(f"Both models returned empty text for user {user_id}")
            final_text = "Manze, both my brains drew a blank on that one. Can you rephrase or try again?"

        # ─── MEMORY EXTRACTION (always via Gemini — it has JSON mode) ───
        if "[MEMORY SAVED]" in final_text:
            try:
                last_msg = conversation_history[-1]
                user_input = " ".join([
                    p if isinstance(p, str) else p.get("text", "")
                    for p in last_msg.get("parts", [])
                    if isinstance(p, str) or (isinstance(p, dict) and "text" in p)
                ])
                extraction = await _call_gemini_with_retry(
                    gemini_client.aio.models.generate_content,
                    model=MODEL_GEMINI,
                    contents=f'Extract the personal fact from this user message: "{user_input}"',
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=UserFact,
                    ),
                    timeout=10,
                )
                raw_json = extraction.text.strip()
                if "```" in raw_json:
                    raw_json = re.sub(r'^```(?:json)?\n?|(?:\n?)+```$', '', raw_json, flags=re.MULTILINE).strip()
                fact_obj = UserFact.model_validate_json(raw_json)
                if fact_obj.confidence > 0.6:
                    sanitized = _sanitize_fact(fact_obj.fact)
                    update_user_fact(user_id, sanitized, fact_obj.category)
                    logger.info(f"Memory saved for {user_id}: {sanitized}")
            except Exception as e:
                logger.error(f"Memory extraction failed: {e}")
            final_text = final_text.replace("[MEMORY SAVED]", "").strip()

        # ─── TAG PROCESSING ───
        # Fallback: If Emily said "here's an image/picture" but forgot the [IMG:] tag,
        # or "here you go" for a GIF but forgot the [GIF:] tag
        if not re.search(r'\[\s*(?:IMG|IMAGE|GIF)', final_text, re.IGNORECASE):
            # Get the user's original request
            last_user_msg = ""
            try:
                for msg in reversed(conversation_history):
                    if msg.get("role") == "user":
                        for p in msg.get("parts", []):
                            if isinstance(p, dict) and "text" in p:
                                last_user_msg = p["text"]
                            elif isinstance(p, str):
                                last_user_msg = p
                        break
            except Exception:
                pass

            user_lower = last_user_msg.lower()

            # GIF fallback — user asked for a GIF but Emily forgot the tag
            if last_user_msg and any(kw in user_lower for kw in ["gif", "animated", "animation"]):
                gif_match = re.search(
                    r'(?:gif|animated\s+(?:image|pic))\s+(?:of\s+)?(.+)',
                    last_user_msg, re.IGNORECASE
                )
                if gif_match:
                    search_term = gif_match.group(1).strip().rstrip('?!.')
                else:
                    # Try broader extraction: "show me a gif of cat dancing"
                    gif_match2 = re.search(r'(?:show|send|share|get)\s+(?:me\s+)?(?:a\s+)?gif\s+(?:of\s+)?(.+)', user_lower)
                    search_term = gif_match2.group(1).strip().rstrip('?!.') if gif_match2 else ""
                if search_term:
                    final_text += f"\n[GIF: {search_term}]"
                    logger.info(f"GIF tag fallback injected: [GIF: {search_term}]")

            # Image fallback — user asked for an image but Emily forgot the tag
            elif last_user_msg and re.search(r"[Hh]ere'?s?\s+(?:an?\s+)?(?:image|picture|photo|pic|him|her|it)\s*(?:of|for|:)?", final_text):
                img_match = re.search(
                    r'(?:picture|image|photo|pic)\s+(?:of\s+)?(.+)',
                    last_user_msg, re.IGNORECASE
                )
                if not img_match:
                    # Broader: "show me Bruno Fernandez"
                    img_match = re.search(
                        r'(?:show|send|share)\s+(?:me\s+)?(?:a\s+)?(?:picture|image|photo|pic)?\s*(?:of\s+)?(.+)',
                        last_user_msg, re.IGNORECASE
                    )
                if img_match:
                    search_term = img_match.group(1).strip().rstrip('?!.')
                    if search_term and len(search_term) > 2:
                        final_text += f"\n[IMG: {search_term}]"
                        logger.info(f"Image tag fallback injected: [IMG: {search_term}]")

        final_text, s = await _process_all_tags(
            r'\[\s*STOCK:\s*(.*?)\s*\]', final_text,
            lambda x: get_stock_price(x) or f"*(Couldn't get price for {x}.)*"
        )
        final_text += s
        final_text, g = await _process_all_tags(
            r'\[\s*GIFS?:\s*(.*?)\s*\]', final_text,
            lambda x: get_media_link(x, is_gif=True) or ""
        )
        final_text += g
        final_text, i = await _process_all_tags(
            r'\[\s*(?:IMAGES?|IMGS?):\s*(.*?)\s*\]', final_text,
            lambda x: get_media_link(x, is_gif=False) or ""
        )
        final_text += i
        final_text, v = await _process_all_tags(
            r'\[\s*VIDEOS?:\s*(.*?)\s*\]', final_text,
            lambda x: search_video_link(x) or ""
        )
        final_text += v

        return final_text, source_links

    except Exception as e:
        logger.error(f"Brain error: {e}", exc_info=True)
        # Notify owner about brain failures
        try:
            await notify_owner(bot, "Brain Error", str(e))
        except Exception:
            pass
        return "Manze, my head is completely jammed. Try again?", ""


# ══════════════════════════════════════════════
# DISCORD BOT
# ══════════════════════════════════════════════
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Needed for welcome messages
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

@bot.event
async def on_ready():
    logger.info(f"Emily connected to Discord: {bot.user}")
    logger.info(f"Hive Mind active: Gemini ({MODEL_GEMINI}) + Claude ({MODEL_CLAUDE})")
    # Start background tasks
    if not check_reminders.is_running():
        check_reminders.start()
    if not daily_news_briefing.is_running():
        daily_news_briefing.start()
    if not weekend_movie_suggestion.is_running():
        weekend_movie_suggestion.start()
    if not monday_music_drop.is_running():
        monday_music_drop.start()
    if not rotate_status.is_running():
        rotate_status.start()
    if not weekly_digest.is_running():
        weekly_digest.start()
    if not daily_birthday_check.is_running():
        daily_birthday_check.start()
    if not accountability_check.is_running():
        accountability_check.start()
    if not daily_learning.is_running():
        daily_learning.start()
    if not weekly_finance_coaching.is_running():
        weekly_finance_coaching.start()
    if not film_tweet.is_running():
        film_tweet.start()
    if not investment_alerts.is_running():
        investment_alerts.start()
    if not watchparty_sms_reminders.is_running():
        watchparty_sms_reminders.start()
    if not weekly_playlist_recs.is_running():
        weekly_playlist_recs.start()


# ══════════════════════════════════════════════
# BACKGROUND TASKS
# ══════════════════════════════════════════════
@tasks.loop(seconds=30)
async def check_reminders():
    """Check for due reminders and watch parties every 30 seconds."""
    try:
        # Personal reminders
        due = get_due_reminders()
        for reminder in due:
            try:
                channel = bot.get_channel(int(reminder["channel_id"]))
                if channel:
                    user_mention = f"<@{reminder['user_id']}>"
                    await channel.send(f"⏰ {user_mention} **Reminder:** {reminder['text']}")
                mark_reminder_done(reminder["_id"])
            except Exception as e:
                logger.error(f"Reminder send error: {e}")
                mark_reminder_done(reminder["_id"])

        # Watch party notifications
        due_parties = get_due_watchparties()
        for party in due_parties:
            try:
                channel = bot.get_channel(int(party["channel_id"]))
                if channel:
                    mentions = " ".join([f"<@{uid}>" for uid in party.get("attendees", [])])
                    await channel.send(
                        f"🍿🎬 **WATCH PARTY TIME!**\n\n"
                        f"**Now showing: {party['title']}**\n"
                        f"{mentions}\n\n"
                        f"Grab your snacks, manze! When you're done, use `!endparty` "
                        f"and then rate it with `!rate {party['title']} <score>`!"
                    )
                start_watchparty(party["_id"])
            except Exception as e:
                logger.error(f"Watch party notify error: {e}")
                start_watchparty(party["_id"])
    except Exception as e:
        logger.error(f"Background task error: {e}")
        try:
            await notify_owner(bot, "Background Task", str(e))
        except Exception:
            pass

@check_reminders.before_loop
async def before_reminders():
    await bot.wait_until_ready()

@tasks.loop(minutes=1)
async def daily_news_briefing():
    """Post daily news at configured time for each server — with AI commentary."""
    try:
        now = datetime.now(pytz.timezone('Africa/Nairobi'))
        current_time = now.strftime("%H:%M")

        servers = get_news_servers()
        for server in servers:
            news_time = server.get("news_time", "07:00")
            if current_time != news_time:
                continue

            # Check if already posted today
            last_posted = server.get("last_news_date", "")
            today = now.strftime("%Y-%m-%d")
            if last_posted == today:
                continue

            channel_id = server.get("news_channel_id")
            if not channel_id:
                continue

            channel = bot.get_channel(int(channel_id))
            if not channel:
                continue

            # Fetch raw news with dedup
            guild_id = server["guild_id"]
            topics = server.get("news_topics", ["Kenya", "business Kenya", "technology Africa"])
            already_sent = get_sent_news_urls(guild_id, days=3)

            all_articles = []
            all_urls = []
            for topic in topics[:3]:
                articles, urls = get_news_raw(topic, max_results=3, exclude_urls=already_sent)
                for a in articles:
                    a["topic"] = topic
                all_articles.extend(articles)
                all_urls.extend(urls)
                already_sent.update(urls)

            if not all_articles:
                continue

            # Generate AI commentary
            headlines = "\n".join([
                f"- [{a.get('title', '')}] ({a.get('source', '')}) — {a.get('snippet', '')[:80]}"
                for a in all_articles[:8]
            ])

            try:
                response = await asyncio.wait_for(
                    claude_client.messages.create(
                        model=MODEL_CLAUDE,
                        max_tokens=800,
                        messages=[{"role": "user", "content": (
                            f"{EMILY_MINI_PERSONA}\n\n"
                            f"Write a morning news briefing for a Kenyan Discord server based on these headlines:\n\n"
                            f"{headlines}\n\n"
                            f"For each major story (pick 4-5 most important):\n"
                            f"1. One-line summary\n"
                            f"2. Your quick take (witty, Kenyan perspective)\n\n"
                            f"Start with a morning greeting. Keep it under 1200 characters. Be punchy."
                        )}],
                    ),
                    timeout=30,
                )
                commentary = ""
                for block in response.content:
                    if block.type == "text":
                        commentary += block.text
            except Exception:
                commentary = None

            # Build briefing
            briefing = "☀️📰 **Good Morning! Emily's Daily Briefing:**\n\n"

            if commentary:
                briefing += commentary
            else:
                for a in all_articles[:6]:
                    briefing += f"• **{a.get('title', '')}** — *{a.get('source', '')}*\n"

            # Add source links
            briefing += "\n\n**Read more:**\n"
            for a in all_articles[:6]:
                title = a.get('title', 'Link')[:45]
                url = a.get('url', '#')
                briefing += f"• [{title}]({url})\n"

            await send_chunked_reply_channel(channel, briefing)

            # Save sent URLs for future dedup
            if all_urls:
                save_sent_news(guild_id, all_urls)

            # Mark as posted
            update_server_setting(guild_id, "last_news_date", today)
            logger.info(f"AI news briefing posted to server {guild_id}")

    except Exception as e:
        logger.error(f"News briefing error: {e}")

@daily_news_briefing.before_loop
async def before_news():
    await bot.wait_until_ready()


@tasks.loop(minutes=1)
async def weekend_movie_suggestion():
    """Suggest a movie every Friday, Saturday, and Sunday evening."""
    try:
        now = datetime.now(pytz.timezone('Africa/Nairobi'))
        current_time = now.strftime("%H:%M")
        day_of_week = now.weekday()  # 0=Monday, 4=Friday, 5=Saturday, 6=Sunday

        # Only run on Friday (4), Saturday (5), Sunday (6)
        if day_of_week not in (4, 5, 6):
            return

        servers = get_movie_suggestion_servers()
        tweeted_movie = False
        for server in servers:
            suggest_time = server.get("suggest_time", "19:00")
            if current_time != suggest_time:
                continue

            # Check if already suggested today
            last_date = server.get("last_suggestion_date", "")
            today = now.strftime("%Y-%m-%d")
            if last_date == today:
                continue

            channel_id = server.get("channel_id")
            if not channel_id:
                continue

            channel = bot.get_channel(int(channel_id))
            if not channel:
                continue

            # Generate suggestion
            suggestion = await _generate_movie_suggestion(str(server["guild_id"]))
            if suggestion:
                await channel.send(suggestion)
                # Mark as suggested today
                from watchparty_tools import movie_settings_col
                if movie_settings_col is not None:
                    movie_settings_col.update_one(
                        {"guild_id": str(server["guild_id"])},
                        {"$set": {"last_suggestion_date": today}}
                    )
                logger.info(f"Movie suggested to server {server['guild_id']}")

                # Also tweet the movie pick (only once, from first server)
                if twitter_configured() and not tweeted_movie:
                    try:
                        # Extract a short tweet from the suggestion
                        import re as _re
                        title_match = _re.search(r'\*\*(.+?)\*\*\s*\((\d{4})\)', suggestion)
                        imdb_match = _re.search(r'IMDB:\*?\*?\s*([\d.]+)', suggestion)
                        rt_match = _re.search(r'Rotten Tomatoes:\*?\*?\s*(\d+%)', suggestion)
                        genre_match = _re.search(r'🎭\s*(.+?)(?:\n|$)', suggestion)
                        director_match = _re.search(r'Directed by:\s*(.+?)(?:\n|$)', suggestion)

                        if title_match:
                            t_title = title_match.group(1)
                            t_year = title_match.group(2)
                            t_imdb = imdb_match.group(1) if imdb_match else None
                            t_rt = rt_match.group(1) if rt_match else None
                            t_genre = genre_match.group(1).strip() if genre_match else None
                            t_director = director_match.group(1).strip() if director_match else None

                            movie_tweet = format_movie_tweet(t_title, t_year, t_genre, t_imdb, t_rt, t_director)
                            await asyncio.to_thread(send_tweet, movie_tweet)
                            tweeted_movie = True
                            logger.info(f"Movie tweeted: {t_title}")
                    except Exception as te:
                        logger.error(f"Movie tweet error: {te}")

    except Exception as e:
        logger.error(f"Movie suggestion error: {e}")
        try:
            await notify_owner(bot, "Movie Suggestion Task", str(e))
        except Exception:
            pass

@weekend_movie_suggestion.before_loop
async def before_movie_suggest():
    await bot.wait_until_ready()


@tasks.loop(minutes=1)
async def monday_music_drop():
    """Post Spotify mood playlist every Monday morning."""
    try:
        now = datetime.now(pytz.timezone('Africa/Nairobi'))
        if now.weekday() != 0 or now.strftime("%H:%M") != "09:00":
            return

        if not spotify_configured():
            return

        today = now.strftime("%Y-%m-%d")
        servers = get_news_servers()

        for server_config in servers:
            guild_id = str(server_config["guild_id"])

            # Use dedicated music channel if set, otherwise fall back to news channel
            settings = get_server_settings(guild_id)
            channel_id = settings.get("music_channel_id") or server_config.get("news_channel_id")
            if not channel_id:
                continue

            last_music = server_config.get("last_music_date", "")
            if last_music == today:
                continue

            channel = bot.get_channel(int(channel_id))
            if not channel:
                continue

            moods = ["chill", "hype", "happy", "workout", "party", "afrobeats", "romantic", "focus"]
            mood = random.choice(moods)
            mood_emoji = {
                "chill": "😌", "hype": "🔥", "happy": "☀️", "workout": "💪",
                "party": "🎉", "afrobeats": "🌍", "romantic": "💕", "focus": "🧠",
            }

            tracks, error = await asyncio.to_thread(get_recommendations, mood, 5)
            if not tracks:
                continue

            lines = [f"🎵 **Emily's Monday Playlist — {mood_emoji.get(mood, '🎧')} {mood.title()} Vibes**\n"]
            lines.append(f"_Start your week right, manze!_\n")
            for i, t in enumerate(tracks, 1):
                lines.append(f"**{i}.** [{t['artists']} — {t['name']}]({t['url']})")

            lines.append(f"\n_Want different vibes? Try `!vibes <mood>`_ 🎧")

            await channel.send("\n".join(lines))
            update_server_setting(guild_id, "last_music_date", today)
            logger.info(f"Monday music for guild {guild_id}: {mood}")

    except Exception as e:
        logger.error(f"Monday music error: {e}")
        try:
            await notify_owner(bot, "Monday Music Task", str(e))
        except Exception:
            pass

@monday_music_drop.before_loop
async def before_monday_music():
    await bot.wait_until_ready()


async def _generate_movie_suggestion(guild_id):
    """Use Gemini with search to find a real movie with ratings."""
    try:
        # Pick random language and genre for variety
        language = random.choice(MOVIE_LANGUAGES)
        genre = random.choice(MOVIE_GENRES)

        # Get past suggestions to avoid repeats
        past = get_past_suggestions(guild_id)
        avoid_text = ""
        if past:
            avoid_text = f"Do NOT suggest any of these (already suggested): {', '.join(past[:20])}"

        prompt = (
            f"Suggest ONE specific {genre} movie/film in {language} language that would be great for a group watch party. "
            f"The movie should be highly rated and well-known enough to have IMDB and Rotten Tomatoes scores. "
            f"It can be from any decade. {avoid_text}\n\n"
            f"You MUST search to find accurate ratings. Return EXACTLY this format:\n"
            f"TITLE: [exact movie title]\n"
            f"YEAR: [year]\n"
            f"LANGUAGE: [language]\n"
            f"GENRE: [genres]\n"
            f"IMDB: [X.X/10]\n"
            f"ROTTEN_TOMATOES: [XX%]\n"
            f"DIRECTOR: [director name]\n"
            f"PLOT: [2-3 sentence plot summary without spoilers]\n"
            f"WHY_WATCH: [1-2 sentences on why this is perfect for a group watch, written as Emily — "
            f"a fun Kenyan cinephile who uses slang like manze, aki, wueh]"
        )

        search_tool = types.Tool(google_search=types.GoogleSearch())
        response = await _call_gemini_with_retry(
            gemini_client.aio.models.generate_content,
            model=MODEL_GEMINI,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
            config=types.GenerateContentConfig(
                tools=[search_tool],
                system_instruction="You are a movie recommendation engine. Always use Google Search to find accurate IMDB and Rotten Tomatoes ratings. Never make up ratings.",
                response_modalities=["TEXT"],
            ),
            timeout=20,
        )

        text = response.text.strip()

        # Parse the structured response
        title = _extract_field(text, "TITLE")
        year = _extract_field(text, "YEAR")
        language_found = _extract_field(text, "LANGUAGE")
        genre_found = _extract_field(text, "GENRE")
        imdb = _extract_field(text, "IMDB")
        rt = _extract_field(text, "ROTTEN_TOMATOES") or _extract_field(text, "ROTTEN TOMATOES")
        director = _extract_field(text, "DIRECTOR")
        plot = _extract_field(text, "PLOT")
        why = _extract_field(text, "WHY_WATCH") or _extract_field(text, "WHY WATCH")

        if not title:
            logger.warning(f"Movie suggestion parse failed: {text[:200]}")
            return None

        # Log to avoid repeats
        log_movie_suggestion(guild_id, title, language_found, year, imdb, rt, genre_found, plot)

        # Build the beautiful message
        day_name = datetime.now(pytz.timezone('Africa/Nairobi')).strftime("%A")
        message = (
            f"🎬🍿 **Emily's {day_name} Movie Pick!**\n\n"
            f"**{title}** ({year})\n"
            f"🎭 {genre_found}\n"
            f"🌍 {language_found}\n"
            f"🎬 Directed by: {director}\n\n"
        )

        if imdb:
            message += f"⭐ **IMDB:** {imdb}\n"
        if rt:
            message += f"🍅 **Rotten Tomatoes:** {rt}\n"

        message += f"\n📖 **Plot:** {plot}\n"

        if why:
            message += f"\n💬 **Emily's take:** *{why}*\n"

        message += (
            f"\n────────────────────\n"
            f"Watched it? Log it: `!watched {title}`\n"
            f"Rate it: `!rate <score> {title}`"
        )

        return message

    except Exception as e:
        logger.error(f"Movie suggestion generation error: {e}")
        return None


def _extract_field(text, field_name):
    """Extract a field value from structured text like 'TITLE: Inception'."""
    pattern = rf'{field_name}:\s*(.+?)(?:\n|$)'
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1).strip().strip('[]')
    return None


# ══════════════════════════════════════════════
# STATUS ROTATION (Emily changes presence by time of day)
# ══════════════════════════════════════════════
@tasks.loop(minutes=15)
async def rotate_status():
    """Update Emily's Discord status based on Nairobi time."""
    try:
        now = datetime.now(pytz.timezone('Africa/Nairobi'))
        hour = now.hour

        if 5 <= hour < 12:
            period = "morning"
        elif 12 <= hour < 17:
            period = "afternoon"
        elif 17 <= hour < 22:
            period = "evening"
        else:
            period = "night"

        status_text = random.choice(EMILY_STATUSES[period])
        await bot.change_presence(activity=discord.CustomActivity(name=status_text))
    except Exception as e:
        logger.error(f"Status rotation error: {e}")

@rotate_status.before_loop
async def before_status():
    await bot.wait_until_ready()


# ══════════════════════════════════════════════
# WEEKLY DIGEST (Sunday evening summary)
# ══════════════════════════════════════════════
@tasks.loop(minutes=1)
async def weekly_digest():
    """Send weekly summary to users on Sunday evening."""
    try:
        now = datetime.now(pytz.timezone('Africa/Nairobi'))
        # Sunday = 6, at 18:00 EAT
        if now.weekday() != 6 or now.strftime("%H:%M") != "18:00":
            return

        from tracker_tools import get_server_settings, server_settings_col
        if server_settings_col is None:
            return

        # Get all servers that have news enabled (we'll reuse as "active servers")
        servers = get_news_servers()
        for server_config in servers:
            try:
                channel_id = server_config.get("news_channel_id")
                if not channel_id:
                    continue

                # Check if already posted this week
                last_digest = server_config.get("last_digest_date", "")
                today = now.strftime("%Y-%m-%d")
                if last_digest == today:
                    continue

                channel = bot.get_channel(int(channel_id))
                if not channel or not channel.guild:
                    continue

                guild_id = str(channel.guild.id)

                # Build digest
                digest = "📋 **Emily's Weekly Roundup!** 🇰🇪\n\n"
                digest += f"_Week ending {now.strftime('%B %d, %Y')}_\n\n"

                # Movies watched this week
                from watchparty_tools import get_watch_history
                history = get_watch_history(guild_id, limit=50)
                week_start = now - timedelta(days=7)
                recent_movies = [m for m in history if m.get("watched_at") and m["watched_at"] >= week_start]
                if recent_movies:
                    digest += f"🎬 **Movies watched this week:** {len(recent_movies)}\n"
                    for m in recent_movies[:5]:
                        digest += f"  • {m['title']}\n"
                    digest += "\n"

                # Top rated
                from watchparty_tools import get_group_top_rated
                top = get_group_top_rated(guild_id, limit=3)
                if top:
                    digest += "🏆 **All-time top rated:**\n"
                    for i, m in enumerate(top):
                        medal = ["🥇", "🥈", "🥉"][i]
                        digest += f"  {medal} {m['title']} ({m['avg_score']:.1f}/10)\n"
                    digest += "\n"

                # Weekly quote
                from utility_tools import get_daily_quote
                digest += get_daily_quote() + "\n\n"

                digest += "_See you next week, manze! 💪_"

                await channel.send(digest)

                # Mark as posted
                update_server_setting(guild_id, "last_digest_date", today)
                logger.info(f"Weekly digest posted for guild {guild_id}")

            except Exception as e:
                logger.error(f"Digest error for server: {e}")

    except Exception as e:
        logger.error(f"Weekly digest loop error: {e}")
        try:
            await notify_owner(bot, "Weekly Digest Task", str(e))
        except Exception:
            pass

@weekly_digest.before_loop
async def before_digest():
    await bot.wait_until_ready()


# ══════════════════════════════════════════════
# DAILY BIRTHDAY / ANNIVERSARY CHECK
# ══════════════════════════════════════════════
@tasks.loop(minutes=1)
async def daily_birthday_check():
    """Check for birthdays/anniversaries every day at 8am EAT."""
    try:
        now = datetime.now(pytz.timezone('Africa/Nairobi'))
        if now.strftime("%H:%M") != "08:00":
            return

        guilds = get_guilds_with_events()
        for guild_id in guilds:
            events = get_todays_events(guild_id)
            if not events:
                continue

            # Find a channel to post in (news channel or general)
            guild = bot.get_guild(int(guild_id))
            if not guild:
                continue

            channel = guild.system_channel
            if not channel:
                for ch in guild.text_channels:
                    if any(n in ch.name.lower() for n in ["general", "chat", "lobby"]):
                        channel = ch
                        break
            if not channel:
                continue

            for event in events:
                if event["event_type"] == "birthday":
                    year = event["date"].year
                    age = now.year - year if year < now.year else ""
                    age_text = f" Turning **{age}**!" if age else ""
                    await channel.send(
                        f"🎂🎉 **Happy Birthday {event['name']}!**{age_text}\n\n"
                        f"Wueh, it's your special day, manze! Everyone show some love! 🥳🎈"
                    )
                else:
                    await channel.send(
                        f"💍✨ **Happy Anniversary {event['name']}!**\n\n"
                        f"Celebrating this milestone today! Congrats! 🥂"
                    )
    except Exception as e:
        logger.error(f"Birthday check error: {e}")

@daily_birthday_check.before_loop
async def before_birthday():
    await bot.wait_until_ready()


# ══════════════════════════════════════════════
# ACCOUNTABILITY CHECK (Wednesday evenings)
# ══════════════════════════════════════════════
@tasks.loop(minutes=1)
async def accountability_check():
    """Nudge users about stale goals every Wednesday at 6pm EAT."""
    try:
        now = datetime.now(pytz.timezone('Africa/Nairobi'))
        if now.weekday() != 2 or now.strftime("%H:%M") != "18:00":
            return

        stale = get_stale_goals(days=5)
        for goal in stale:
            try:
                user = bot.get_user(int(goal["user_id"]))
                if user:
                    msg = generate_accountability_message(goal)
                    await user.send(f"⏰ **Accountability Check!**\n\n{msg}")
            except Exception as e:
                logger.error(f"Accountability DM error: {e}")
    except Exception as e:
        logger.error(f"Accountability check error: {e}")

@accountability_check.before_loop
async def before_accountability():
    await bot.wait_until_ready()


# ══════════════════════════════════════════════
# DAILY LEARNING (posts at 12pm EAT)
# ══════════════════════════════════════════════
@tasks.loop(minutes=1)
async def daily_learning():
    """Post a daily learning nugget at noon EAT."""
    try:
        now = datetime.now(pytz.timezone('Africa/Nairobi'))
        if now.strftime("%H:%M") != "12:00":
            return

        servers = get_news_servers()  # Reuse news channel config
        for server_config in servers:
            channel_id = server_config.get("news_channel_id")
            if not channel_id:
                continue

            last_learn = server_config.get("last_learn_date", "")
            today = now.strftime("%Y-%m-%d")
            if last_learn == today:
                continue

            channel = bot.get_channel(int(channel_id))
            if not channel:
                continue

            # Rotate category: Mon/Thu=finance, Tue/Fri=cooking, Wed/Sat=film, Sun=random
            day = now.weekday()
            if day in (0, 3):
                cat = "finance"
            elif day in (1, 4):
                cat = "cooking"
            elif day in (2, 5):
                cat = "film"
            else:
                cat = random.choice(["finance", "cooking", "film"])

            topic = random.choice(LEARNING_TOPICS[cat])
            cat_emoji = {"finance": "💰", "cooking": "🍳", "film": "🎬"}[cat]

            # Use Claude to generate the lesson
            try:
                lesson_response = await asyncio.wait_for(
                    claude_client.messages.create(
                        model=MODEL_CLAUDE,
                        max_tokens=1024,
                        system=f"{EMILY_MINI_PERSONA} Write a fun, educational 3-4 paragraph lesson. Include real-world examples and a practical tip someone can use TODAY.",
                        messages=[{"role": "user", "content": f"Teach me about: {topic}"}],
                    ),
                    timeout=API_TIMEOUT_SECONDS,
                )
                lesson_text = ""
                for block in lesson_response.content:
                    if block.type == "text":
                        lesson_text += block.text

                if lesson_text:
                    message = (
                        f"{cat_emoji} **Emily's Daily Lesson — {cat.title()}**\n\n"
                        f"**Today's topic:** {topic}\n\n"
                        f"{lesson_text}\n\n"
                        f"_Learn something new every day with Emily!_ 📚"
                    )
                    await send_chunked_reply_to_channel(channel, message)
                    update_server_setting(str(server_config["guild_id"]), "last_learn_date", today)
            except Exception as e:
                logger.error(f"Learning lesson generation error: {e}")

    except Exception as e:
        logger.error(f"Daily learning error: {e}")
        try:
            await notify_owner(bot, "Daily Learning Task", str(e))
        except Exception:
            pass

@daily_learning.before_loop
async def before_learning():
    await bot.wait_until_ready()


# ══════════════════════════════════════════════
# WEEKLY FINANCE COACHING (Saturday 6pm EAT)
# ══════════════════════════════════════════════
@tasks.loop(minutes=1)
async def weekly_finance_coaching():
    """Analyze spending and share personalized finance tips every Saturday."""
    try:
        now = datetime.now(pytz.timezone('Africa/Nairobi'))
        # Saturday = 5, at 18:00 EAT
        if now.weekday() != 5 or now.strftime("%H:%M") != "18:00":
            return

        today = now.strftime("%Y-%m-%d")
        servers = get_news_servers()

        for server_config in servers:
            guild_id = str(server_config["guild_id"])
            settings = get_server_settings(guild_id)

            # Use dedicated finance channel if set, otherwise news channel
            channel_id = settings.get("finance_channel_id") or server_config.get("news_channel_id")
            if not channel_id:
                continue

            last_coach = settings.get("last_finance_coaching", "")
            if last_coach == today:
                continue

            channel = bot.get_channel(int(channel_id))
            if not channel:
                continue

            guild = bot.get_guild(int(guild_id))
            if not guild:
                continue

            # Find all users who have logged expenses this month
            from tracker_tools import budgets_col
            if budgets_col is None:
                continue

            month_str = now.strftime("%Y-%m")
            user_ids = budgets_col.distinct("user_id", {"month_str": month_str})

            if not user_ids:
                continue

            # Build spending summaries for all active users
            user_summaries = []
            for uid in user_ids:
                member = guild.get_member(int(uid))
                if not member:
                    continue

                monthly = get_monthly_spending(uid, month_str)
                if not monthly or monthly["total"] == 0:
                    continue

                limit = get_budget_limit(uid)
                name = member.display_name

                # Build summary text
                cats = monthly.get("by_category", {})
                sorted_cats = sorted(cats.items(), key=lambda x: -x[1])
                cat_text = ", ".join([f"{c}: KES {a:,.0f}" for c, a in sorted_cats[:5]])

                summary = f"**{name}:** KES {monthly['total']:,.0f} total ({monthly['count']} transactions). Top: {cat_text}."
                if limit:
                    remaining = limit - monthly['total']
                    pct = (monthly['total'] / limit) * 100
                    summary += f" Budget: {pct:.0f}% used (KES {remaining:,.0f} left)."

                user_summaries.append(summary)

            if not user_summaries:
                continue

            # Use Claude to generate personalized tips
            spending_data = "\n".join(user_summaries)
            day_of_month = now.day
            days_left = 30 - day_of_month

            prompt = (
                f"{EMILY_MINI_PERSONA} It's Saturday evening — time for your weekly finance check-in. "
                f"You're reviewing your community's spending for {now.strftime('%B %Y')}. "
                f"We're {day_of_month} days in with ~{days_left} days left.\n\n"
                f"Spending data:\n{spending_data}\n\n"
                f"Write a 3-4 paragraph weekly finance coaching message. Include:\n"
                f"1. Overall observation — who's doing well, who might need to watch out\n"
                f"2. Specific tips based on their top spending categories (if someone spends a lot on food, suggest meal prepping; on transport, suggest alternatives)\n"
                f"3. A practical Kenyan-specific money saving tip (M-Shwari, SACCOs, Naivas vs Carrefour deals, etc.)\n"
                f"4. A motivational closing with a Kenyan proverb about money\n\n"
                f"Keep it warm, practical, and fun. Use Kenyan slang. Don't be preachy — be like a friend who's good with money."
            )

            try:
                response = await asyncio.wait_for(
                    claude_client.messages.create(
                        model=MODEL_CLAUDE,
                        max_tokens=1500,
                        messages=[{"role": "user", "content": prompt}],
                    ),
                    timeout=API_TIMEOUT_SECONDS,
                )
                tips_text = ""
                for block in response.content:
                    if block.type == "text":
                        tips_text += block.text

                if tips_text:
                    message = f"💰 **Emily's Weekly Finance Check-In** 📊\n\n{tips_text}"
                    await send_chunked_reply_to_channel(channel, message)
                    update_server_setting(guild_id, "last_finance_coaching", today)
                    logger.info(f"Finance coaching posted for guild {guild_id}")

            except Exception as e:
                logger.error(f"Finance coaching generation error: {e}")

    except Exception as e:
        logger.error(f"Finance coaching task error: {e}")
        try:
            await notify_owner(bot, "Finance Coaching Task", str(e))
        except Exception:
            pass

@weekly_finance_coaching.before_loop
async def before_finance_coaching():
    await bot.wait_until_ready()


# ══════════════════════════════════════════════
# FILM TWEET (random thoughts on films, 2x per week)
# ══════════════════════════════════════════════
@tasks.loop(minutes=1)
async def film_tweet():
    """Post a film hot take / recommendation tweet twice a week at random times."""
    try:
        now = datetime.now(pytz.timezone('Africa/Nairobi'))

        # Check if today is one of the 2 film tweet days this week
        if not is_film_tweet_day():
            return

        # Check if it's the posting time for this week
        if now.strftime("%H:%M") != get_film_tweet_time():
            return

        if not twitter_configured():
            return

        # Get a random film prompt and have Claude generate the tweet
        prompt = get_film_tweet_prompt()

        # Search for current film news to keep tweets relevant
        search_context = ""
        try:
            search_results = await asyncio.to_thread(
                get_search_results, "trending movies 2026 new releases box office", 2
            )
            if search_results:
                content = await asyncio.to_thread(extract_text_from_url, search_results[0], 800)
                if content:
                    search_context = (
                        f"\n\nHere is CURRENT film news for context:\n{content}\n\n"
                        "You can reference this current info if relevant to your tweet, "
                        "but you don't have to. Feel free to tweet about any film topic."
                    )
        except Exception as e:
            logger.warning(f"Film tweet search failed: {e}")

        try:
            response = await asyncio.wait_for(
                claude_client.messages.create(
                    model=MODEL_CLAUDE,
                    max_tokens=200,
                    messages=[{"role": "user", "content": (
                        f"{EMILY_MINI_PERSONA} You're tweeting about film. "
                        f"{prompt} "
                        f"Write ONLY the tweet text, max 260 characters. "
                        f"Be punchy, opinionated, and authentic. Use 1-2 relevant hashtags. "
                        f"Don't use quotes around it. No preamble. "
                        f"Do NOT tweet about Nairobi traffic.{search_context}"
                    )}],
                ),
                timeout=API_TIMEOUT_SECONDS,
            )

            tweet_text = ""
            for block in response.content:
                if block.type == "text":
                    tweet_text += block.text

            tweet_text = tweet_text.strip().strip('"')

            if len(tweet_text) > 280:
                tweet_text = tweet_text[:277] + "..."

            if not tweet_text:
                logger.warning("Film tweet: Claude returned empty text")
                return

            success, result = await asyncio.to_thread(send_tweet, tweet_text)
            if success:
                logger.info(f"Film tweet posted: {result}")
            else:
                logger.error(f"Film tweet failed: {result}")

        except asyncio.TimeoutError:
            logger.error("Film tweet: Claude timed out")
        except Exception as e:
            logger.error(f"Film tweet generation error: {e}")

    except Exception as e:
        logger.error(f"Film tweet task error: {e}")
        try:
            await notify_owner(bot, "Film Tweet Task", str(e))
        except Exception:
            pass


@film_tweet.before_loop
async def before_film_tweet():
    await bot.wait_until_ready()


# ══════════════════════════════════════════════
# INVESTMENT ALERTS (check every 30 minutes)
# ══════════════════════════════════════════════
@tasks.loop(minutes=30)
async def investment_alerts():
    """Check portfolio stocks and alert users on big price moves."""
    try:
        alert_users = await asyncio.to_thread(get_all_alert_users)
        if not alert_users:
            return

        for alert in alert_users:
            try:
                user_id = alert["user_id"]
                channel_id = alert.get("channel_id")
                threshold = alert.get("threshold_pct", 5.0)

                holdings = await asyncio.to_thread(get_portfolio, user_id)
                if not holdings:
                    continue

                last_prices = await asyncio.to_thread(get_last_prices, user_id)
                current_prices = {}
                alerts_to_send = []

                for h in holdings:
                    ticker = h["ticker"]
                    try:
                        stock_data = await asyncio.to_thread(get_stock_price, ticker)
                        if not stock_data or "couldn't find" in stock_data:
                            continue

                        # Parse price from formatted string
                        import re
                        price_match = re.search(r'Price:\s*([\d,]+\.?\d*)', stock_data)
                        if not price_match:
                            continue

                        current_price = float(price_match.group(1).replace(",", ""))
                        current_prices[ticker] = current_price

                        # Compare with last known price
                        last_price = last_prices.get(ticker)
                        if last_price and last_price > 0:
                            change_pct = ((current_price - last_price) / last_price) * 100

                            if abs(change_pct) >= threshold:
                                direction = "📈" if change_pct > 0 else "📉"
                                sign = "+" if change_pct > 0 else ""
                                alerts_to_send.append(
                                    f"{direction} **{ticker}**: {sign}{change_pct:.1f}% "
                                    f"(was {last_price:,.2f} → now {current_price:,.2f})"
                                )

                    except Exception as e:
                        logger.warning(f"Alert check failed for {ticker}: {e}")
                        continue

                # Save current prices for next comparison
                if current_prices:
                    await asyncio.to_thread(save_last_prices, user_id, current_prices)

                # Send alerts
                if alerts_to_send and channel_id:
                    try:
                        channel = bot.get_channel(int(channel_id))
                        if channel:
                            alert_msg = (
                                f"🔔 **Investment Alert** <@{user_id}>\n\n"
                                + "\n".join(alerts_to_send)
                                + f"\n\n_Threshold: {threshold}% | Update with `!setalert <pct>`_"
                            )
                            await channel.send(alert_msg)
                            logger.info(f"Investment alert sent to {user_id}: {len(alerts_to_send)} alerts")
                    except Exception as e:
                        logger.warning(f"Couldn't send alert to channel {channel_id}: {e}")

                # Brief delay between users
                await asyncio.sleep(3)

            except Exception as e:
                logger.error(f"Investment alert error for {alert.get('user_id')}: {e}")

    except Exception as e:
        logger.error(f"Investment alerts task error: {e}")


@investment_alerts.before_loop
async def before_investment_alerts():
    await bot.wait_until_ready()


# ══════════════════════════════════════════════
# WATCHPARTY SMS REMINDERS (4 days before)
# ══════════════════════════════════════════════
@tasks.loop(hours=6)
async def watchparty_sms_reminders():
    """Check for upcoming watch parties and send SMS reminders 4 days before."""
    try:
        if not messaging_configured():
            return

        # Check all guilds for upcoming watch parties
        for guild in bot.guilds:
            guild_id = str(guild.id)

            next_wp = get_next_watchparty(guild_id)
            if not next_wp:
                continue

            wp_time = next_wp.get("time")
            wp_title = next_wp.get("title", "Movie Night")
            wp_id = str(next_wp.get("_id", ""))

            if not wp_time:
                continue

            # Check if it's ~4 days away (between 3.5 and 4.5 days)
            now = datetime.now(pytz.timezone('Africa/Nairobi'))
            if hasattr(wp_time, 'tzinfo') and wp_time.tzinfo is None:
                wp_time = pytz.timezone('Africa/Nairobi').localize(wp_time)

            days_until = (wp_time - now).total_seconds() / 86400
            if not (3.5 <= days_until <= 4.5):
                continue

            # Check if we already sent reminders for this party
            if wp_id and get_reminder_log(guild_id, wp_id):
                continue

            contacts = get_contacts(guild_id)
            if not contacts:
                continue

            # Search for trailer
            trailer_link = ""
            try:
                trailer_link = await asyncio.to_thread(search_video_link, f"{wp_title} official trailer")
            except Exception:
                pass

            # Generate and send unique reminders
            wp_date = wp_time.strftime("%A, %b %d at %I:%M %p")
            sent_count = 0

            for contact in contacts:
                name = contact.get("name", "Friend")
                phone = contact.get("phone", "")
                if not phone:
                    continue

                # Generate unique message via Claude
                try:
                    response = await asyncio.wait_for(
                        claude_client.messages.create(
                            model=MODEL_CLAUDE,
                            max_tokens=150,
                            messages=[{"role": "user", "content": (
                                f"{EMILY_MINI_PERSONA} Write a short, fun SMS reminder (max 140 chars) to {name}. "
                                f"Remind them that the watch party for '{wp_title}' is in 4 days on {wp_date}. "
                                f"Make it personal — like texting a friend. No hashtags. Just the text."
                            )}],
                        ),
                        timeout=15,
                    )
                    msg = ""
                    for block in response.content:
                        if block.type == "text":
                            msg += block.text
                    msg = msg.strip().strip('"')
                except Exception:
                    msg = f"Yo {name}! Reminder — {wp_title} watch party is in 4 days ({wp_date}). Mark your calendar, manze!"

                if trailer_link:
                    msg += f"\n\nTrailer: {trailer_link}"

                success, _ = await asyncio.to_thread(send_sms, phone, msg)
                if success:
                    sent_count += 1
                await asyncio.sleep(2)

            # Log that reminders were sent
            if wp_id and sent_count > 0:
                log_reminder_sent(guild_id, wp_id)
                logger.info(f"Watchparty reminders sent: {sent_count} contacts for '{wp_title}' in guild {guild_id}")

    except Exception as e:
        logger.error(f"Watchparty SMS reminder error: {e}")
        try:
            await notify_owner(bot, "Watchparty SMS Reminder", str(e))
        except Exception:
            pass


@watchparty_sms_reminders.before_loop
async def before_watchparty_sms_reminders():
    await bot.wait_until_ready()


# ══════════════════════════════════════════════
# WEEKLY MUSIC RECOMMENDATIONS (every Monday)
# ══════════════════════════════════════════════
@tasks.loop(minutes=1)
async def weekly_playlist_recs():
    """Send weekly music recommendations based on saved artists every Monday at 10am EAT."""
    try:
        now = datetime.now(pytz.timezone('Africa/Nairobi'))

        # Monday at 10:00 AM EAT
        if now.weekday() != 0 or now.strftime("%H:%M") != "10:00":
            return

        if not spotify_configured():
            return

        users = await asyncio.to_thread(get_all_weekly_music_users)
        if not users:
            return

        for saved in users:
            try:
                user_id = saved["user_id"]
                artists = saved.get("artists", [])
                channel_id = saved.get("channel_id")
                if not artists:
                    continue

                result, error = await asyncio.to_thread(
                    get_recs_from_artists, artists, 7
                )
                if error:
                    logger.warning(f"Weekly rec failed for {user_id}: {error}")
                    continue

                message_text = f"<@{user_id}> 🎵\n\n" + format_weekly_recommendations(result)

                # Post to saved channel if available
                sent = False
                if channel_id:
                    try:
                        channel = bot.get_channel(int(channel_id))
                        if channel:
                            await send_chunked_reply_to_channel(channel, message_text)
                            logger.info(f"Weekly music rec posted to channel {channel_id} for {user_id}")
                            sent = True
                    except Exception as e:
                        logger.warning(f"Couldn't post weekly rec to channel {channel_id}: {e}")

                # Fallback to DM if channel posting failed
                if not sent:
                    try:
                        user = bot.get_user(int(user_id))
                        if not user:
                            user = await bot.fetch_user(int(user_id))
                        if user:
                            await user.send(format_weekly_recommendations(result))
                            logger.info(f"Weekly music rec DM'd to {user_id}")
                    except Exception as e:
                        logger.warning(f"Couldn't DM weekly rec to {user_id}: {e}")

                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Weekly rec error for user {saved.get('user_id')}: {e}")

    except Exception as e:
        logger.error(f"Weekly music recs task error: {e}")
        try:
            await notify_owner(bot, "Weekly Music Recs Task", str(e))
        except Exception:
            pass


@weekly_playlist_recs.before_loop
async def before_weekly_playlist_recs():
    await bot.wait_until_ready()


async def send_chunked_reply_to_channel(channel, text):
    """Send a long message to a channel (not as a reply)."""
    while len(text) > 2000:
        split_at = text.rfind('\n', 0, 2000)
        if split_at == -1:
            split_at = 2000
        await channel.send(text[:split_at])
        text = text[split_at:].lstrip()
    if text:
        await channel.send(text)


# ══════════════════════════════════════════════
# WELCOME MESSAGES
# ══════════════════════════════════════════════
@bot.event
async def on_member_join(member):
    """Greet new server members in Emily's style."""
    try:
        # Find the system/welcome channel
        channel = member.guild.system_channel
        if not channel:
            # Try to find a #general or #welcome channel
            for ch in member.guild.text_channels:
                if any(name in ch.name.lower() for name in ["general", "welcome", "lobby", "chat"]):
                    channel = ch
                    break

        if not channel:
            return

        welcome_text = random.choice([
            f"Sasa {member.mention}! 👋 Welcome to **{member.guild.name}**!",
            f"Wueh! {member.mention} just walked in! 🎉 Welcome to **{member.guild.name}**!",
            f"Niaje {member.mention}! 😊 Welcome to **{member.guild.name}**!",
            f"Aki, look who's here! {member.mention} welcome to **{member.guild.name}**! 🙌",
        ])

        welcome_text += """

I'm **Emily** — your AI movie buddy, finance advisor, and foodie! 🍿

**🎬 Watch Party:**
• `!watched <title>` — Log a movie you watched
• `!rate <score> <title>` — Rate a movie (1-10)
• `!ratings <title>` — See group ratings
• `!toprated` — Group's best-rated movies
• `!filmnight` — Full overview + stats
• `!suggest` — Get a movie suggestion now
• `!trivia` — Start a trivia game

Type `!help` for all my commands!"""

        await channel.send(welcome_text)
    except Exception as e:
        logger.error(f"Welcome message error: {e}")


# ══════════════════════════════════════════════
# BOT COMMANDS (!help, !budget, !portfolio, !remind, !news, !reset, !forget)
# ══════════════════════════════════════════════
@bot.command(name="help")
async def cmd_help(ctx):
    """Show all available commands."""
    page1 = """**Emily's Commands** 🇰🇪 **(1/2)**

**💰 Budget:** `!spent 500 lunch` · `!income 50000 freelance` · `!delincome` · `!budget` · `!setbudget 50000` · `!report` · `!financetip`
**📈 Portfolio:** `!buy SCOM 100 25` · `!sell SCOM` · `!portfolio`
**💱 Finance:** `!convert` · `!loan` · `!mshwari` · `!bankloan <lender> <amt> <months>` · `!compareloan <amt> <months>`

**🎬 Watch Party:**
`!watched` · `!rate` · `!ratings` · `!toprated` · `!filmnight`
`!suggest` · `!setmovienight` · `!addmovie` · `!watchlist`
`!vote` · `!watchparty` · `!join` · `!endparty`

**🎵 Spotify:** `!song <query>` · `!vibes <mood>` · `!mytaste <artists>` · `!myrec`

**⏰ Reminders:** `!remind 5pm call mum` · `!reminders`"""

    page2 = """**Emily's Commands** 🇰🇪 **(2/2)**

**📰 Fun:** `!news` · `!newsbrief` · `!setnews` · `!quote` · `!trivia` · `!roast` · `!debate` · `!learn`

**📱 Reddit:**
`!reddit <subreddit>` · `!wsb` · `!investbuzz` · `!stockreddit <ticker>` · `!rsearch <topic>`

**🎯 Goals:** `!goal` · `!savinggoal` · `!goals` · `!saved` · `!addsaved` · `!progress` · `!done`

**🎂 Dates:** `!birthday <name> <date>` · `!anniversary <name> <date>` · `!birthdays`

**🎙️ Settings:** `!voicemode` · `!voicechat` · `!setpersona` · `!reset` · `!forget` · `!setfinance` · `!setmusic` · `!help`

**🐦 Twitter:** `!tweet <text>` · `!emilytweet <topic>`

**💻 Code:** `!review <code or file>` · `!explain <code or file>`

**🔔 Alerts:** `!setalert 5` · `!stopalert`

**📱 SMS:** `!addphone <n> <num>` · `!removephone <n>` · `!contacts` · `!notifywp <movie>`

**📝 To-Do:** `!todo <task>` · `!todos` · `!done 1` · `!deltodo 1` · `!cleartodos`

**⏰ Reminders:** `!remind 5pm call mum` · `!reminders` · `!cancelremind 1`

_Or just @ mention me to chat!_ 😊"""

    await ctx.send(page1)
    await ctx.send(page2)

@bot.command(name="spent")
async def cmd_spent(ctx, amount: str, *, description: str = "General expense"):
    """Log an expense. Usage: !spent 500 lunch at Java"""
    try:
        amt = float(amount.replace(",", "").replace("KES", "").replace("ksh", "").strip())
        if amt <= 0:
            await ctx.reply("Manze, that's not a valid amount!")
            return
        
        # Try to detect category from description
        category = _detect_expense_category(description)
        
        if log_expense(str(ctx.author.id), amt, description, category):
            daily = get_daily_spending(str(ctx.author.id))
            today_total = daily["total"] if daily else amt

            # Emily-style commentary based on category and amount
            import random as _rnd
            if category == "food" and amt > 1000:
                comment = _rnd.choice(["Wueh, fine dining today!", "Manze, that's a proper meal!", "Aki, someone's eating good!"])
            elif category == "food":
                comment = _rnd.choice(["Sawa, a person must eat!", "Noted!", "Fiti."])
            elif category == "transport" and amt > 500:
                comment = _rnd.choice(["Uber life, eh?", "Matatu would've been 70 bob, just saying 😏", "Traffic must've been bad."])
            elif category == "shopping":
                comment = _rnd.choice(["Retail therapy?", "Hope it was worth it!", "Treat yourself, manze."])
            elif category == "entertainment":
                comment = _rnd.choice(["Living your best life!", "Fun costs money, sawa.", "You deserve it!"])
            elif amt > 5000:
                comment = _rnd.choice(["Big spend! Hope it's worth it.", "Wueh, heavy one.", "That's a chunk — noted."])
            else:
                comment = _rnd.choice(["Noted!", "Sawa!", "Logged!", "Got it!"])

            await ctx.reply(f"✅ **KES {amt:,.2f}** — {description} ({category})\n{comment}\n📊 Today: **KES {today_total:,.2f}**")
        else:
            await ctx.reply("Eish, couldn't save that. Try again?")
    except ValueError:
        await ctx.reply("That amount doesn't look right. Try: `!spent 500 lunch`")


@bot.command(name="budget")
async def cmd_budget(ctx):
    """View budget summary including income."""
    summary = format_full_budget_summary(str(ctx.author.id))
    await ctx.send(summary)


@bot.command(name="setbudget")
async def cmd_setbudget(ctx, amount: str):
    """Set monthly budget limit."""
    try:
        amt = float(amount.replace(",", "").replace("KES", "").replace("ksh", "").strip())
        if set_budget_limit(str(ctx.author.id), amt):
            await ctx.reply(f"✅ Monthly budget set to **KES {amt:,.2f}**. I'll keep you accountable, manze!")
        else:
            await ctx.reply("Couldn't set budget. Try again?")
    except ValueError:
        await ctx.reply("Invalid amount. Try: `!setbudget 50000`")


@bot.command(name="income")
async def cmd_income(ctx, amount: str, source: str = "freelance", *, description: str = ""):
    """Log income. Usage: !income 50000 freelance web design project"""
    try:
        amt = float(amount.replace(",", "").replace("KES", "").replace("ksh", "").strip())
        if amt <= 0:
            await ctx.reply("Manze, that's not a valid amount!")
            return

        # Normalize source
        source_lower = source.lower()
        valid_sources = list(INCOME_CATEGORIES.keys())
        if source_lower not in valid_sources:
            # If the source isn't a known category, treat it as part of the description
            description = f"{source} {description}".strip()
            source_lower = "freelance"

        if log_income(str(ctx.author.id), amt, source_lower, description):
            monthly_income = get_monthly_income(str(ctx.author.id))
            month_total = monthly_income["total"] if monthly_income else amt

            label = INCOME_CATEGORIES.get(source_lower, f"💰 {source_lower.title()}")
            desc_text = f" — {description}" if description else ""

            # Emily-style commentary
            import random as _rnd
            if amt >= 100000:
                comment = _rnd.choice([
                    "Manze, big bag alert! 💰🔥",
                    "Wueh, someone's getting paid!",
                    "Now THAT'S what I like to see!",
                ])
            elif amt >= 30000:
                comment = _rnd.choice([
                    "Nice one! The hustle is paying off.",
                    "Fiti! Money moving.",
                    "Sawa, secure the bag! 💪",
                ])
            else:
                comment = _rnd.choice([
                    "Every shilling counts!",
                    "Noted! Keep stacking.",
                    "Fiti, logged it!",
                ])

            effective = get_effective_budget(str(ctx.author.id))
            budget_note = ""
            if effective:
                monthly_spent = get_monthly_spending(str(ctx.author.id))
                spent = monthly_spent["total"] if monthly_spent else 0
                remaining = effective - spent
                budget_note = f"\n📋 Available: **KES {remaining:,.2f}** this month"

            await ctx.reply(
                f"✅ {label}: **KES {amt:,.2f}**{desc_text}\n"
                f"{comment}\n"
                f"💰 Month income: **KES {month_total:,.2f}**{budget_note}"
            )
        else:
            await ctx.reply("Eish, couldn't save that. Try again?")
    except ValueError:
        await ctx.reply("Invalid amount. Try: `!income 50000 freelance web project`")


@bot.command(name="delincome")
async def cmd_delincome(ctx):
    """Delete the most recent income entry. Usage: !delincome"""
    deleted = delete_last_income(str(ctx.author.id))
    if deleted:
        amt = deleted["amount"]
        src = deleted.get("source", "")
        desc = deleted.get("description", "")
        label = INCOME_CATEGORIES.get(src, f"💰 {src.title()}")
        desc_text = f" — {desc}" if desc else ""

        monthly_inc = get_monthly_income(str(ctx.author.id))
        month_total = monthly_inc["total"] if monthly_inc else 0

        await ctx.reply(
            f"🗑️ Deleted: {label} **KES {amt:,.2f}**{desc_text}\n"
            f"💰 Month income now: **KES {month_total:,.2f}**"
        )
    else:
        await ctx.reply("No income entries to delete!")


@bot.command(name="buy")
async def cmd_buy(ctx, ticker: str, shares: str, price: str):
    """Add a stock holding. Usage: !buy SCOM 100 25.50"""
    try:
        s = float(shares)
        p = float(price.replace(",", ""))
        if add_holding(str(ctx.author.id), ticker.upper(), s, p):
            total = s * p
            await ctx.reply(f"✅ Added: **{s:.0f} shares of {ticker.upper()}** at KES {p:,.2f} (Total: KES {total:,.2f})")
        else:
            await ctx.reply("Couldn't add that holding. Try again?")
    except ValueError:
        await ctx.reply("Format: `!buy SCOM 100 25.50`")


@bot.command(name="sell")
async def cmd_sell(ctx, ticker: str):
    """Remove a stock holding."""
    if remove_holding(str(ctx.author.id), ticker.upper()):
        await ctx.reply(f"✅ Removed **{ticker.upper()}** from your portfolio.")
    else:
        await ctx.reply(f"Couldn't find {ticker.upper()} in your portfolio.")


@bot.command(name="portfolio")
async def cmd_portfolio(ctx):
    """View portfolio."""
    summary = format_portfolio(str(ctx.author.id))
    await ctx.send(summary)


@bot.command(name="remind")
async def cmd_remind(ctx, *, reminder_text: str):
    """Set a reminder. Usage: !remind 5pm call mum | !remind in 2 hours check oven"""
    try:
        eat_zone = pytz.timezone('Africa/Nairobi')
        parsed_time = None
        message = ""

        # Strategy: try parsing progressively longer prefixes to find the time part
        words = reminder_text.split()
        for i in range(len(words), 0, -1):
            time_part = " ".join(words[:i])
            parsed = dateparser.parse(
                time_part,
                settings={
                    'PREFER_DATES_FROM': 'future',
                    'TIMEZONE': 'Africa/Nairobi',
                    'RETURN_AS_TIMEZONE_AWARE': True,
                }
            )
            if parsed:
                parsed_time = parsed
                message = " ".join(words[i:]).strip()
                break

        # If nothing worked, try the full string as a last resort
        if not parsed_time:
            parsed_time = dateparser.parse(
                reminder_text,
                settings={
                    'PREFER_DATES_FROM': 'future',
                    'TIMEZONE': 'Africa/Nairobi',
                    'RETURN_AS_TIMEZONE_AWARE': True,
                }
            )
            if parsed_time:
                # Try to extract message by removing time words
                message = reminder_text
                time_words = ["in", "at", "on", "tomorrow", "today", "tonight",
                              "hour", "hours", "minute", "minutes", "min", "mins"]
                for w in time_words:
                    message = re.sub(rf'\b{w}\b', '', message, flags=re.IGNORECASE)
                message = re.sub(r'\b\d{1,2}:\d{2}\b', '', message)
                message = re.sub(r'\b\d{1,2}\s*(?:am|pm)\b', '', message, flags=re.IGNORECASE)
                message = message.strip(' ,.-')

        if not parsed_time:
            await ctx.reply("Couldn't figure out the time. Try:\n`!remind 5pm call mum`\n`!remind in 2 hours check oven`\n`!remind tomorrow 9am fix code`")
            return

        if not message:
            message = "Reminder!"

        if add_reminder(str(ctx.author.id), str(ctx.channel.id), parsed_time, message):
            time_str = parsed_time.strftime("%I:%M %p on %b %d")
            await ctx.reply(f"⏰ Sawa! I'll remind you: **{message}** at **{time_str}** (EAT)")
        else:
            await ctx.reply("Couldn't set that reminder. Try again?")
    except Exception as e:
        logger.error(f"Remind error: {e}")
        await ctx.reply("Something went wrong. Try: `!remind 5pm call mum`")


@bot.command(name="reminders")
async def cmd_reminders(ctx):
    """List pending reminders."""
    reminders = get_user_reminders(str(ctx.author.id))
    if not reminders:
        await ctx.reply("No pending reminders! Set one with `!remind 5pm do something` or tell me naturally.")
        return
    lines = ["⏰ **Your Reminders:**\n"]
    for i, r in enumerate(reminders, 1):
        time_str = r["remind_at"].strftime("%I:%M %p, %b %d")
        lines.append(f"**{i}.** {r['text']} — {time_str}")
    lines.append(f"\n_Cancel: `!cancelremind 1`_")
    await ctx.send("\n".join(lines))


@bot.command(name="cancelremind")
async def cmd_cancelremind(ctx, index: int = 0):
    """Cancel a pending reminder. Usage: !cancelremind 1"""
    if index < 1:
        await ctx.reply("Which reminder? Check `!reminders` for the list, then `!cancelremind 1`")
        return
    text = cancel_reminder(str(ctx.author.id), index)
    if text:
        await ctx.reply(f"🗑️ Cancelled: **{text}**")
    else:
        await ctx.reply(f"Couldn't find reminder #{index}. Check `!reminders`")


# ══════════════════════════════════════════════
# TO-DO LIST
# ══════════════════════════════════════════════
@bot.command(name="todo")
async def cmd_todo(ctx, *, text: str = ""):
    """Add a to-do item or view your list. Usage: !todo buy groceries"""
    user_id = str(ctx.author.id)

    if not text:
        # Show list
        todos = get_todos(user_id, include_done=True)
        await ctx.send(format_todos(todos))
        return

    # Check for priority prefix
    priority = "normal"
    if text.startswith("!") or text.startswith("urgent"):
        priority = "high"
        text = text.lstrip("!").replace("urgent ", "", 1).strip()
    elif text.startswith("*"):
        priority = "medium"
        text = text.lstrip("*").strip()

    position = add_todo(user_id, text, priority)
    if position:
        icon = "🔴" if priority == "high" else "🟡" if priority == "medium" else "⬜"
        await ctx.reply(f"{icon} Added to-do #{position}: **{text}**")
    else:
        await ctx.reply("Couldn't add that. Try again?")


@bot.command(name="todos")
async def cmd_todos(ctx):
    """View your to-do list."""
    todos = get_todos(str(ctx.author.id), include_done=True)
    await ctx.send(format_todos(todos))


@bot.command(name="done")
async def cmd_done(ctx, index: int = 0):
    """Mark a to-do as done. Usage: !done 1"""
    if index < 1:
        await ctx.reply("Which one? Check `!todos` then `!done 1`")
        return
    text = complete_todo(str(ctx.author.id), index)
    if text:
        await ctx.reply(f"✅ Done: ~~{text}~~")
    else:
        await ctx.reply(f"Couldn't find to-do #{index}. Check `!todos`")


@bot.command(name="deltodo")
async def cmd_deltodo(ctx, index: int = 0):
    """Delete a to-do item. Usage: !deltodo 1"""
    if index < 1:
        await ctx.reply("Which one? Check `!todos` then `!deltodo 1`")
        return
    text = remove_todo(str(ctx.author.id), index)
    if text:
        await ctx.reply(f"🗑️ Removed: **{text}**")
    else:
        await ctx.reply(f"Couldn't find to-do #{index}. Check `!todos`")


@bot.command(name="cleartodos")
async def cmd_cleartodos(ctx):
    """Clear all completed to-do items."""
    count = clear_done_todos(str(ctx.author.id))
    if count > 0:
        await ctx.reply(f"🧹 Cleared {count} completed item{'s' if count != 1 else ''}!")
    else:
        await ctx.reply("No completed items to clear.")


@bot.command(name="news")
async def cmd_news(ctx, *, topic: str = "Kenya"):
    """Get latest news. Usage: !news | !news technology | !news sports"""
    async with ctx.typing():
        news, _ = get_latest_news(topic, max_results=5)
        if news:
            await send_chunked_reply(ctx.message, news)
        else:
            await ctx.reply(f"Couldn't fetch news for '{topic}' right now. Try again?")


@bot.command(name="newsbrief")
async def cmd_newsbrief(ctx, *, topics: str = ""):
    """Get an AI-powered Kenyan news digest with Emily's commentary.
    Usage: !newsbrief | !newsbrief tech, sports | !newsbrief politics"""
    async with ctx.typing():
        try:
            # Parse topics or use defaults
            if topics:
                topic_list = [t.strip() for t in topics.split(",") if t.strip()]
            else:
                topic_list = ["Kenya", "business Kenya", "technology Africa"]

            # Fetch raw news for each topic
            guild_id = str(ctx.guild.id) if ctx.guild else "dm"
            already_sent = get_sent_news_urls(guild_id, days=3) if ctx.guild else set()

            all_articles = []
            all_urls = []
            for topic in topic_list[:4]:  # Max 4 topics
                articles, urls = await asyncio.to_thread(
                    get_news_raw, topic, max_results=3, exclude_urls=already_sent
                )
                for article in articles:
                    article["topic"] = topic
                all_articles.extend(articles)
                all_urls.extend(urls)
                already_sent.update(urls)

            if not all_articles:
                await ctx.reply("Couldn't fetch any news right now. Try again later?")
                return

            # Build headlines summary for Claude
            headlines = "\n".join([
                f"- [{a.get('title', 'No title')}] ({a.get('source', 'Unknown')}) — {a.get('snippet', '')[:100]}"
                for a in all_articles[:10]
            ])

            # Generate AI commentary
            commentary_prompt = (
                f"{EMILY_MINI_PERSONA}\n\n"
                f"Here are today's news headlines:\n\n{headlines}\n\n"
                f"Write a Kenyan-style news briefing based on these headlines. For each major story:\n"
                f"1. Summarize what happened in 1-2 sentences\n"
                f"2. Add your personal take / commentary (witty, insightful, Kenyan perspective)\n"
                f"3. Rate the story's importance (🔥 big deal, 👀 interesting, 💤 meh)\n\n"
                f"Start with a greeting like 'Habari za leo!' and end with a witty sign-off.\n"
                f"Keep the whole briefing under 1500 characters. Be punchy and opinionated."
            )

            try:
                response = await asyncio.wait_for(
                    claude_client.messages.create(
                        model=MODEL_CLAUDE,
                        max_tokens=800,
                        messages=[{"role": "user", "content": commentary_prompt}],
                    ),
                    timeout=30,
                )
                commentary = ""
                for block in response.content:
                    if block.type == "text":
                        commentary += block.text
            except Exception as e:
                logger.error(f"News commentary generation error: {e}")
                commentary = None

            # Build final output
            briefing = "📰🇰🇪 **Emily's News Briefing**\n\n"

            if commentary:
                briefing += commentary
            else:
                # Fallback — just list the headlines without AI commentary
                briefing += "Here's what's happening:\n\n"
                for a in all_articles[:8]:
                    briefing += f"• **{a.get('title', '')}** — *{a.get('source', '')}*\n"

            # Add source links at the bottom
            briefing += "\n\n**Sources:**\n"
            for a in all_articles[:8]:
                title = a.get('title', 'Link')[:50]
                url = a.get('url', '#')
                briefing += f"• [{title}]({url})\n"

            # Save sent URLs for dedup
            if ctx.guild and all_urls:
                save_sent_news(guild_id, all_urls)

            await send_chunked_reply(ctx.message, briefing)

        except Exception as e:
            logger.error(f"Newsbrief error: {e}")
            await ctx.reply(f"Something went wrong: {e}")


@bot.command(name="setnews")
async def cmd_setnews(ctx, *, config: str = ""):
    """Set up daily AI news briefing. Usage: !setnews | !setnews 8:00 tech, sports, Kenya"""
    try:
        if not ctx.guild:
            await ctx.reply("This only works in a server, not DMs!")
            return

        guild_id = str(ctx.guild.id)
        channel_id = str(ctx.channel.id)

        # Parse time and topics
        news_time = "07:00"
        topics = ["Kenya", "business Kenya", "technology Africa"]

        if config:
            # Check if first part is a time
            parts = config.split(None, 1)
            time_match = re.match(r'^(\d{1,2}):(\d{2})$', parts[0])
            if time_match:
                news_time = parts[0]
                if len(parts) > 1:
                    topics = [t.strip() for t in parts[1].split(",") if t.strip()]
            else:
                # No time — treat everything as topics
                topics = [t.strip() for t in config.split(",") if t.strip()]

        if set_news_channel(guild_id, channel_id):
            update_server_setting(guild_id, "news_time", news_time)
            update_server_setting(guild_id, "news_topics", topics)

            topic_str = ", ".join(topics[:4])
            await ctx.reply(
                f"✅ **Daily AI News Briefing set!**\n\n"
                f"📍 Channel: <#{channel_id}>\n"
                f"⏰ Time: **{news_time} EAT** daily\n"
                f"📰 Topics: **{topic_str}**\n\n"
                f"Emily will fetch headlines and add her Kenyan commentary every morning!\n"
                f"Want one now? Try `!newsbrief` or `!news`"
            )
        else:
            await ctx.reply("Couldn't set up news. Try again?")
    except Exception as e:
        logger.error(f"Setnews error: {e}")
        await ctx.reply(f"Error setting up news: {e}")


@bot.command(name="reset")
async def cmd_reset(ctx):
    """Clear chat history."""
    from memory import clear_chat_history
    clear_chat_history(str(ctx.author.id))
    await ctx.reply("🗑️ Chat history cleared! Fresh start, manze.")


@bot.command(name="forget")
async def cmd_forget(ctx):
    """Clear Emily's memory about you."""
    from memory import clear_user_facts
    clear_user_facts(str(ctx.author.id))
    await ctx.reply("🧠 I've forgotten everything about you. We're strangers now, but not for long!")


@bot.command(name="convert")
async def cmd_convert(ctx, amount: str, from_curr: str, to_curr: str = "KES"):
    """Convert currency. Usage: !convert 100 USD KES"""
    try:
        amt = float(amount.replace(",", ""))
        # Handle "to" keyword: !convert 100 USD to KES
        if from_curr.lower() == "to":
            await ctx.reply("Format: `!convert 100 USD KES`")
            return
        if to_curr.lower() == "to" and len(ctx.message.content.split()) > 4:
            to_curr = ctx.message.content.split()[-1]

        result, error = convert_currency(amt, from_curr, to_curr)
        if result:
            await ctx.send(format_currency_result(result))
        else:
            await ctx.reply(f"Couldn't convert: {error}")
    except ValueError:
        await ctx.reply("Format: `!convert 100 USD KES`")


@bot.command(name="loan")
async def cmd_loan(ctx, principal: str, rate: str, months: str, loan_type: str = "reducing"):
    """Calculate loan repayment. Usage: !loan 500000 14 12 [reducing/flat]"""
    try:
        result, error = calculate_loan(
            float(principal.replace(",", "")),
            float(rate),
            int(months),
            loan_type.lower()
        )
        if result:
            await ctx.send(format_loan_result(result))
        else:
            await ctx.reply(f"Couldn't calculate: {error}")
    except ValueError:
        await ctx.reply("Format: `!loan 500000 14 12` (principal, rate%, months)\nAdd `flat` for flat rate: `!loan 500000 14 12 flat`")


@bot.command(name="mshwari")
async def cmd_mshwari(ctx, amount: str, days: str = "30"):
    """Calculate M-Shwari loan cost. Usage: !mshwari 5000 [days]"""
    try:
        result, error = calculate_mshwari(float(amount.replace(",", "")), int(days))
        if result:
            await ctx.send(format_mshwari_result(result))
        else:
            await ctx.reply(f"Couldn't calculate: {error}")
    except ValueError:
        await ctx.reply("Format: `!mshwari 5000` or `!mshwari 5000 60` (for 60 days)")


@bot.command(name="bankloan")
async def cmd_bankloan(ctx, lender: str, amount: str, months: str = "12"):
    """Calculate loan from a Kenyan bank/SACCO. Usage: !bankloan stima 500000 24"""
    try:
        # Look up lender
        lender_key = LENDER_ALIASES.get(lender.lower().strip(), lender.lower().strip())
        if lender_key not in KENYAN_LENDERS:
            available_banks = [v["name"] for k, v in KENYAN_LENDERS.items() if v["type"] == "bank"]
            available_saccos = [v["name"] for k, v in KENYAN_LENDERS.items() if v["type"] == "sacco"]
            available_mobile = [v["name"] for k, v in KENYAN_LENDERS.items() if v["type"] == "mobile"]
            await ctx.reply(
                f"Unknown lender: **{lender}**\n\n"
                f"**🏦 Banks:** {', '.join(available_banks)}\n"
                f"**🤝 SACCOs:** {', '.join(available_saccos)}\n"
                f"**📱 Mobile:** {', '.join(available_mobile)}\n\n"
                f"Example: `!bankloan stima 500000 24`"
            )
            return

        amt = float(amount.replace(",", "").replace("KES", "").replace("ksh", "").strip())
        m = int(months)

        result, error = calculate_kenyan_loan(lender_key, amt, m)
        if result:
            await ctx.send(format_kenyan_loan(result))
        else:
            await ctx.reply(f"Couldn't calculate: {error}")
    except ValueError:
        await ctx.reply("Format: `!bankloan stima 500000 24` (lender, amount, months)")


@bot.command(name="compareloan")
async def cmd_compareloan(ctx, amount: str, months: str = "12"):
    """Compare loan costs across Kenyan lenders. Usage: !compareloan 500000 24"""
    async with ctx.typing():
        try:
            amt = float(amount.replace(",", "").replace("KES", "").replace("ksh", "").strip())
            m = int(months)

            # Compare banks and SACCOs for longer terms, mobile for short terms
            if m <= 1:
                keys = ["mshwari", "kcb-mpesa", "fuliza", "tala", "branch"]
            else:
                keys = ["stima", "kcb", "equity", "coop", "absa", "im"]

            results = compare_lenders(amt, m, keys)
            if results:
                await send_chunked_reply(ctx.message, format_comparison(results, amt, m))
            else:
                await ctx.reply("Couldn't compare lenders. Try again?")
        except ValueError:
            await ctx.reply("Format: `!compareloan 500000 24` (amount, months)")


@bot.command(name="report")
async def cmd_report(ctx):
    """Generate PDF expense report for this month."""
    async with ctx.typing():
        try:
            user_id = str(ctx.author.id)
            monthly = get_monthly_spending(user_id)

            if not monthly or not monthly.get("entries"):
                await ctx.reply("No expenses this month to report! Start logging with `!spent`")
                return

            # Use effective budget (base limit + income) not just base limit
            limit = get_effective_budget(user_id)
            income = get_monthly_income(user_id)

            user_name = ctx.author.display_name
            pdf_bytes = generate_expense_pdf(user_name, monthly, limit, income_data=income)

            if pdf_bytes:
                now = datetime.now(pytz.timezone('Africa/Nairobi'))
                filename = f"expense_report_{now.strftime('%B_%Y')}.pdf"
                file = discord.File(io.BytesIO(pdf_bytes), filename=filename)
                await ctx.reply(f"📄 Here's your expense report, {user_name}!", file=file)
            else:
                await ctx.reply("Couldn't generate the PDF. Try again?")
        except Exception as e:
            logger.error(f"Report error: {e}")
            await ctx.reply(f"Report generation failed: {e}")


@bot.command(name="recat")
async def cmd_recat(ctx):
    """Recategorize all expenses this month with improved detection. Usage: !recat"""
    async with ctx.typing():
        try:
            user_id = str(ctx.author.id)
            result = recategorize_expenses(user_id, _detect_expense_category)

            if result is None:
                await ctx.reply("Couldn't recategorize. Try again?")
                return

            if result["updated"] == 0:
                await ctx.reply("✅ All your expenses are already correctly categorized!")
                return

            report = f"🔄 **Recategorized {result['updated']}/{result['total']} expenses!**\n\n"
            for change, count in sorted(result["by_category"].items(), key=lambda x: -x[1]):
                report += f"• {change}: **{count}** items\n"

            report += f"\nRun `!report` to see the updated breakdown."
            await ctx.reply(report)

        except Exception as e:
            logger.error(f"Recat error: {e}")
            await ctx.reply(f"Something went wrong: {e}")


@bot.command(name="setfinance")
async def cmd_setfinance(ctx):
    """Set this channel for weekly finance coaching tips."""
    if not ctx.guild:
        await ctx.reply("This only works in a server!")
        return
    update_server_setting(str(ctx.guild.id), "finance_channel_id", str(ctx.channel.id))
    await ctx.reply("✅ Weekly finance coaching will be posted **here** every Saturday at 6pm EAT!\nEmily will analyze your spending and give personalized tips. 💰")


@bot.command(name="financetip")
async def cmd_financetip(ctx):
    """Get personalized finance tips based on your spending right now."""
    async with ctx.typing():
        try:
            user_id = str(ctx.author.id)
            monthly = get_monthly_spending(user_id)

            if not monthly or monthly["total"] == 0:
                await ctx.reply("No spending data yet! Log expenses with `!spent` first, then I can give you tips.")
                return

            limit = get_budget_limit(user_id)
            now = datetime.now(pytz.timezone('Africa/Nairobi'))

            # Build spending summary
            cats = monthly.get("by_category", {})
            sorted_cats = sorted(cats.items(), key=lambda x: -x[1])
            cat_text = "\n".join([f"- {c}: KES {a:,.0f}" for c, a in sorted_cats])

            day_of_month = now.day
            days_left = 30 - day_of_month
            daily_avg = monthly["total"] / max(day_of_month, 1)

            budget_info = ""
            if limit:
                remaining = limit - monthly['total']
                pct = (monthly['total'] / limit) * 100
                daily_allowance = remaining / max(days_left, 1)
                budget_info = (
                    f"\nBudget: KES {limit:,.0f} | Used: {pct:.0f}% | "
                    f"Remaining: KES {remaining:,.0f} | Daily allowance: KES {daily_allowance:,.0f}"
                )

            prompt = (
                f"{EMILY_MINI_PERSONA} Someone just asked for your financial advice. "
                f"Review their spending for {now.strftime('%B %Y')} ({day_of_month} days in, {days_left} days left):\n\n"
                f"Total spent: KES {monthly['total']:,.0f} ({monthly['count']} transactions)\n"
                f"Daily average: KES {daily_avg:,.0f}\n"
                f"Breakdown:\n{cat_text}\n"
                f"{budget_info}\n\n"
                f"Give personalized, practical financial advice in 3-4 paragraphs:\n"
                f"1. How they're doing overall — be honest but kind\n"
                f"2. Specific tips for their top spending categories (suggest Kenyan alternatives like "
                f"cooking at home vs Java, matatu vs uber, Naivas deals, etc.)\n"
                f"3. A practical saving strategy they can start TODAY\n"
                f"4. End with encouragement and a money-related Kenyan proverb\n\n"
                f"Use Kenyan slang naturally. Be like a smart friend, not a bank manager."
            )

            response = await asyncio.wait_for(
                claude_client.messages.create(
                    model=MODEL_CLAUDE,
                    max_tokens=1200,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=API_TIMEOUT_SECONDS,
            )
            tips = ""
            for block in response.content:
                if block.type == "text":
                    tips += block.text

            if tips:
                await send_chunked_reply(ctx.message, f"💰 **Emily's Finance Tips for {ctx.author.display_name}:**\n\n{tips}")
            else:
                await ctx.reply("Couldn't generate tips right now. Try again!")
        except Exception as e:
            logger.error(f"Finance tip error: {e}")
            await ctx.reply("Finance tip engine jammed. Try again!")


@bot.command(name="quote")
async def cmd_quote(ctx):
    """Get a random Kenyan proverb or motivational quote."""
    quote = get_daily_quote()
    await ctx.send(quote)


@bot.command(name="music")
async def cmd_music(ctx, *, mood: str = "chill"):
    """Get music recommendations. Usage: !music chill | !music workout | !music kenyan"""
    async with ctx.typing():
        try:
            # Use Claude for music recommendations
            eat_zone = pytz.timezone('Africa/Nairobi')
            prompt = (
                f"{EMILY_MINI_PERSONA} You're also a music lover. Recommend 5 songs/artists for the mood: '{mood}'. "
                f"Include a mix of Kenyan/African and international music. "
                f"For each song, give: Artist - Song Title and a one-line reason why. "
                f"Keep it fun and opinionated. Use Kenyan slang. "
                f"End with a YouTube search suggestion."
            )
            response = await asyncio.wait_for(
                claude_client.messages.create(
                    model=MODEL_CLAUDE,
                    max_tokens=1024,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=API_TIMEOUT_SECONDS,
            )
            text = ""
            for block in response.content:
                if block.type == "text":
                    text += block.text
            if text:
                await send_chunked_reply(ctx.message, f"🎵 **Music for: {mood}**\n\n{text}")
            else:
                await ctx.reply("Couldn't think of recommendations right now. Try again?")
        except Exception as e:
            logger.error(f"Music recommendation error: {e}")
            # Fallback to basic recommendations
            await ctx.reply(
                f"🎵 **Quick picks for '{mood}':**\n"
                f"• Sauti Sol - Suzanna (always a vibe)\n"
                f"• Burna Boy - Last Last (Afrobeats mood)\n"
                f"• Tems - Free Mind (smooth & easy)\n"
                f"• Bien - Basi (Kenyan classic)\n"
                f"• Nyashinski - Malaika (feel-good)\n\n"
                f"Search YouTube for more '{mood}' playlists, manze!"
            )


# ══════════════════════════════════════════════
# WATCH PARTY COMMANDS
# ══════════════════════════════════════════════
@bot.command(name="addmovie")
async def cmd_addmovie(ctx, *, title: str):
    """Add a movie to the group watchlist. Usage: !addmovie Inception"""
    if not ctx.guild:
        await ctx.reply("Watch parties are for servers, not DMs!")
        return
    result = add_to_watchlist(str(ctx.guild.id), title, str(ctx.author.id))
    if result == "duplicate":
        await ctx.reply(f"**{title}** is already on the watchlist!")
    elif result:
        count = len(get_watchlist(str(ctx.guild.id)))
        await ctx.reply(f"🎬 Added **{title}** to the watchlist! ({count} movies total)\nVote for it: `!vote {title}`")
    else:
        await ctx.reply("Couldn't add that. Try again?")


@bot.command(name="removemovie")
async def cmd_removemovie(ctx, *, title: str):
    """Remove a movie from the watchlist."""
    if not ctx.guild:
        return
    if remove_from_watchlist(str(ctx.guild.id), title):
        await ctx.reply(f"🗑️ Removed **{title}** from the watchlist.")
    else:
        await ctx.reply(f"Couldn't find **{title}** on the watchlist.")


@bot.command(name="watchlist")
async def cmd_watchlist(ctx):
    """View the group watchlist."""
    if not ctx.guild:
        return
    await ctx.send(format_watchlist(str(ctx.guild.id)))


@bot.command(name="vote")
async def cmd_vote(ctx, *, title: str):
    """Vote for a movie on the watchlist."""
    if not ctx.guild:
        return
    success, result = vote_for_movie(str(ctx.guild.id), title, str(ctx.author.id))
    if success:
        await ctx.reply(f"🗳️ Voted for **{title}**! ({result} total votes)")
    else:
        await ctx.reply(f"Couldn't vote: {result}")


@bot.command(name="topvoted")
async def cmd_topvoted(ctx):
    """See the most voted movies."""
    if not ctx.guild:
        return
    top = get_top_voted(str(ctx.guild.id))
    if not top:
        await ctx.reply("No votes yet! Vote with `!vote <title>`")
        return
    lines = ["🗳️ **Most Voted:**\n"]
    for i, m in enumerate(top, 1):
        lines.append(f"**{i}.** {m['title']} — {m['vote_count']} vote{'s' if m['vote_count'] != 1 else ''}")
    await ctx.send("\n".join(lines))


@bot.command(name="rate")
async def cmd_rate(ctx, score: str, *, title: str):
    """Rate a movie 1-10. Usage: !rate 8 Inception"""
    if not ctx.guild:
        return
    try:
        s = int(score)
        if not (1 <= s <= 10):
            await ctx.reply("Score must be 1-10, manze!")
            return
        result = rate_movie(str(ctx.guild.id), title, str(ctx.author.id), s)
        if result == "updated":
            await ctx.reply(f"⭐ Updated your rating for **{title}** to **{s}/10**")
        elif result:
            await ctx.reply(f"⭐ Rated **{title}**: **{s}/10**! See all ratings: `!ratings {title}`")
        else:
            await ctx.reply("Couldn't save that rating.")
    except ValueError:
        await ctx.reply("Format: `!rate 8 Inception`")


@bot.command(name="ratings")
async def cmd_ratings(ctx, *, title: str):
    """View all ratings for a movie."""
    if not ctx.guild:
        return
    await ctx.send(format_ratings(str(ctx.guild.id), title))


@bot.command(name="toprated")
async def cmd_toprated(ctx):
    """See the group's highest-rated movies."""
    if not ctx.guild:
        return
    await ctx.send(format_top_rated(str(ctx.guild.id)))


@bot.command(name="watched")
async def cmd_watched(ctx, *, title: str = None):
    """Log a movie as watched, or view watch history. Usage: !watched Inception"""
    if not ctx.guild:
        return
    if title:
        guild_id = str(ctx.guild.id)
        # First try marking existing watchlist entry
        if not mark_as_watched(guild_id, title):
            # Not on watchlist — add it directly as watched
            add_to_watchlist(guild_id, title, str(ctx.author.id))
            mark_as_watched(guild_id, title)
        
        # Get average rating if any exist
        ratings = get_movie_ratings(guild_id, title)
        rating_text = ""
        if ratings:
            avg = sum(r["score"] for r in ratings) / len(ratings)
            rating_text = f" (Group average: **{avg:.1f}/10**)"
        
        await ctx.reply(
            f"✅ **{title}** logged as watched!{rating_text}\n"
            f"Rate it: `!rate <score> {title}` (e.g. `!rate 8 {title}`)"
        )
    else:
        await ctx.send(format_watch_history(str(ctx.guild.id)))


@bot.command(name="watchparty")
async def cmd_watchparty(ctx, *, args: str = None):
    """Schedule a watch party. Usage: !watchparty Inception tonight 8pm"""
    if not ctx.guild:
        return

    if not args:
        # Show next scheduled party
        party = get_next_watchparty(str(ctx.guild.id))
        await ctx.send(format_watchparty(party))
        return

    try:
        # Parse: title + time
        # Try to extract time from the end of the string
        eat_zone = pytz.timezone('Africa/Nairobi')
        parsed_time = dateparser.parse(
            args,
            settings={
                'PREFER_DATES_FROM': 'future',
                'TIMEZONE': 'Africa/Nairobi',
                'RETURN_AS_TIMEZONE_AWARE': True,
            }
        )

        if not parsed_time:
            await ctx.reply("Couldn't figure out the time. Try: `!watchparty Inception tonight 8pm`")
            return

        # Extract title (remove time-related words)
        title = args
        time_words = ["tonight", "tomorrow", "today", "at", "on", "pm", "am",
                     "saturday", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday"]
        for w in time_words:
            title = re.sub(rf'\b{w}\b', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\b\d{1,2}:\d{2}\b', '', title)
        title = re.sub(r'\b\d{1,2}\s*(?:am|pm)\b', '', title, flags=re.IGNORECASE)
        title = title.strip(' ,.-')

        if not title:
            await ctx.reply("What movie? Try: `!watchparty Inception tonight 8pm`")
            return

        if schedule_watchparty(str(ctx.guild.id), str(ctx.channel.id), title, parsed_time, str(ctx.author.id)):
            time_str = parsed_time.strftime("%A, %b %d at %I:%M %p EAT")
            await ctx.send(
                f"🍿 **Watch Party Scheduled!**\n\n"
                f"**Movie:** {title}\n"
                f"**When:** {time_str}\n"
                f"**Host:** {ctx.author.display_name}\n\n"
                f"Join with `!join` — Emily will ping everyone when it's time!"
            )

            # Auto-send SMS notifications if contacts exist
            if messaging_configured():
                contacts = get_contacts(str(ctx.guild.id))
                if contacts:
                    trailer_link = ""
                    try:
                        trailer_link = await asyncio.to_thread(search_video_link, f"{title} official trailer")
                    except Exception:
                        pass

                    sent_count = 0
                    for contact in contacts:
                        name = contact.get("name", "Friend")
                        phone = contact.get("phone", "")
                        if not phone:
                            continue

                        msg = f"Sasa {name}! Watch party is ON — we're watching {title} on {time_str}. Be there, manze!"
                        if trailer_link:
                            msg += f"\n\nTrailer: {trailer_link}"

                        success, _ = await asyncio.to_thread(send_sms, phone, msg)
                        if success:
                            sent_count += 1
                        await asyncio.sleep(1)

                    if sent_count > 0:
                        await ctx.send(f"📱 SMS sent to **{sent_count}** contacts!")
        else:
            await ctx.reply("Couldn't schedule that. Try again?")
    except Exception as e:
        logger.error(f"Watch party error: {e}")
        await ctx.reply(f"Something went wrong: {e}")


@bot.command(name="join")
async def cmd_join(ctx):
    """Join the next scheduled watch party."""
    if not ctx.guild:
        return
    success, result = join_watchparty(str(ctx.guild.id), str(ctx.author.id))
    if success:
        await ctx.reply(f"🍿 You're in for **{result}**! See you there, manze!")
    else:
        await ctx.reply(result)


@bot.command(name="endparty")
async def cmd_endparty(ctx):
    """End the current watch party and prompt for ratings."""
    if not ctx.guild:
        return
    party = end_watchparty(str(ctx.guild.id))
    if party:
        await ctx.send(
            f"🎬 **Watch party ended: {party['title']}**\n\n"
            f"Hope you enjoyed it, manze! Now rate it:\n"
            f"`!rate <score> {party['title']}`\n\n"
            f"Example: `!rate 8 {party['title']}`"
        )
    else:
        await ctx.reply("No active watch party to end.")


@bot.command(name="filmnight")
async def cmd_filmnight(ctx):
    """Film night overview — watch history, top rated, and group stats."""
    if not ctx.guild:
        return
    async with ctx.typing():
        guild_id = str(ctx.guild.id)
        history = get_watch_history(guild_id, limit=10)
        top = get_group_top_rated(guild_id, limit=5)

        response = "🎬🍿 **Film Night Overview!**\n\n"

        # Top rated
        if top:
            response += "🏆 **Group's Top Rated:**\n"
            for i, m in enumerate(top, 1):
                medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"**{i}.**"
                response += f"{medal} **{m['title']}** — {m['avg_score']:.1f}/10 ({m['num_ratings']} ratings)\n"
            response += "\n"

        # Recent history
        if history:
            response += "📼 **Recently Watched:**\n"
            for m in history[:5]:
                date = m.get("watched_at", m.get("added_at", datetime.now(pytz.timezone('Africa/Nairobi')))).strftime("%b %d")
                response += f"• **{m['title']}** — {date}\n"
            response += "\n"

        # Stats
        all_history = get_watch_history(guild_id, limit=100)
        response += f"📊 **Stats:** {len(all_history)} movies watched together\n\n"

        response += "**Quick actions:**\n"
        response += "• `!watched <title>` — Log a movie you just watched\n"
        response += "• `!rate <score> <title>` — Rate it (1-10)\n"
        response += "• `!toprated` — Full rankings\n"
        response += "• `!ratings <title>` — See everyone's ratings\n"

        await send_chunked_reply(ctx.message, response)


@bot.command(name="setmovienight")
async def cmd_setmovienight(ctx, time: str = "19:00"):
    """Set this channel for weekend movie suggestions. Usage: !setmovienight 19:00"""
    if not ctx.guild:
        await ctx.reply("This only works in a server!")
        return
    try:
        # Validate time format
        if not re.match(r'^\d{1,2}:\d{2}$', time):
            await ctx.reply("Time format should be HH:MM (24hr), e.g. `!setmovienight 19:00`")
            return

        if set_movie_channel(str(ctx.guild.id), str(ctx.channel.id), time):
            await ctx.reply(
                f"✅ **Movie night configured!**\n\n"
                f"Every **Friday, Saturday & Sunday** at **{time} EAT**, "
                f"I'll suggest a movie with IMDB & Rotten Tomatoes ratings right here!\n\n"
                f"Languages: English, French, German, Spanish, Korean\n"
                f"Want one now? Try `!suggest`"
            )
        else:
            await ctx.reply("Couldn't set that up. Try again?")
    except Exception as e:
        logger.error(f"Set movie night error: {e}")
        await ctx.reply(f"Error: {e}")


@bot.command(name="suggest")
async def cmd_suggest(ctx):
    """Get a movie suggestion right now (doesn't wait for the weekend)."""
    if not ctx.guild:
        await ctx.reply("This only works in a server!")
        return
    async with ctx.typing():
        suggestion = await _generate_movie_suggestion(str(ctx.guild.id))
        if suggestion:
            await send_chunked_reply(ctx.message, suggestion)
        else:
            await ctx.reply("Couldn't come up with a suggestion right now. Try again, manze!")


# ══════════════════════════════════════════════
# TRIVIA GAME COMMANDS
# ══════════════════════════════════════════════
@bot.command(name="trivia")
async def cmd_trivia(ctx, category: str = "mixed"):
    """Start a trivia game. Usage: !trivia [movie/finance/food/mixed]"""
    if not ctx.guild:
        await ctx.reply("Trivia is for servers, not DMs!")
        return

    category = category.lower()
    if category not in ("movie", "finance", "food", "mixed"):
        await ctx.reply("Categories: `movie`, `finance`, `food`, or `mixed`")
        return

    # Check if game already active
    if get_game(str(ctx.guild.id)):
        await ctx.reply("A trivia game is already running! Wait for it to finish.")
        return

    total_questions = 5
    game = start_game(str(ctx.guild.id), category, total_questions)

    cat_name = CATEGORY_NAMES.get(category, "Trivia")
    await ctx.send(
        f"🎮 **{cat_name} starting!**\n"
        f"**{total_questions} questions** — React with your answer!\n"
        f"First correct answer gets the point. Let's go, manze! 🔥\n"
        f"─────────────────"
    )

    # Run through questions
    for q_num in range(1, total_questions + 1):
        game["current"] = q_num
        game["answered"] = set()

        trivia = get_trivia_question(category)
        question_text = format_trivia_question(trivia, category, q_num, total_questions)

        q_msg = await ctx.send(question_text)

        # Add reaction options
        for i in range(len(trivia["options"])):
            await q_msg.add_reaction(EMOJI_OPTIONS[i])

        # Wait for answers (15 seconds)
        def check(reaction, user):
            return (
                reaction.message.id == q_msg.id
                and user != bot.user
                and str(reaction.emoji) in EMOJI_OPTIONS[:len(trivia["options"])]
                and str(user.id) not in game["answered"]
            )

        answered_users = []
        end_time = asyncio.get_event_loop().time() + 15

        while asyncio.get_event_loop().time() < end_time:
            try:
                remaining = end_time - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                reaction, user = await bot.wait_for('reaction_add', timeout=remaining, check=check)
                emoji_index = EMOJI_OPTIONS.index(str(reaction.emoji))
                is_correct = emoji_index == trivia["correct_index"]
                game["answered"].add(str(user.id))
                record_answer(str(ctx.guild.id), str(user.id), is_correct)
                answered_users.append((user.display_name, is_correct))
            except asyncio.TimeoutError:
                break

        # Reveal answer
        correct_emoji = EMOJI_OPTIONS[trivia["correct_index"]]
        result_text = f"\n✅ **Answer: {correct_emoji} {trivia['correct_answer']}**\n"

        if answered_users:
            correct_names = [name for name, correct in answered_users if correct]
            wrong_names = [name for name, correct in answered_users if not correct]
            if correct_names:
                result_text += f"🎯 Got it right: {', '.join(correct_names)}\n"
            if wrong_names:
                result_text += f"❌ Missed it: {', '.join(wrong_names)}\n"
        else:
            result_text += "_No one answered! 😅_\n"

        await ctx.send(result_text)

        if q_num < total_questions:
            await asyncio.sleep(3)

    # Game over — show scores
    game = end_game(str(ctx.guild.id))
    scores_text = format_scores(game)
    await ctx.send(f"\n─────────────────\n🏁 **Game Over!**\n\n{scores_text}")


# ══════════════════════════════════════════════
# VOICE MODE TOGGLE
# ══════════════════════════════════════════════
@bot.command(name="voicemode")
async def cmd_voicemode(ctx):
    """Toggle voice mode — Emily auto-sends voice replies."""
    user_id = str(ctx.author.id)
    if user_id in _voice_mode_users:
        _voice_mode_users.discard(user_id)
        await ctx.reply("🔇 Voice mode **OFF**. I'll reply with text only now.")
    else:
        _voice_mode_users.add(user_id)
        await ctx.reply("🎙️ Voice mode **ON**! I'll send voice notes with my replies. Say `!voicemode` again to turn off.")


# ══════════════════════════════════════════════
# GOAL TRACKER COMMANDS
# ══════════════════════════════════════════════
@bot.command(name="goal")
async def cmd_goal(ctx, *, goal_text: str):
    """Set a new goal. Usage: !goal Save 100K by December"""
    if add_goal(str(ctx.author.id), goal_text):
        goals = get_active_goals(str(ctx.author.id))
        await ctx.reply(f"🎯 Goal set: **{goal_text}**\nYou now have **{len(goals)}** active goal(s). Let's get it, manze! 💪")
    else:
        await ctx.reply("Couldn't save that goal. Try again?")


@bot.command(name="goals")
async def cmd_goals(ctx):
    """View your goals."""
    await ctx.send(format_goals(str(ctx.author.id)))


@bot.command(name="progress")
async def cmd_progress(ctx, goal_num: str, percent: str):
    """Update goal progress. Usage: !progress 1 50"""
    try:
        idx = int(goal_num) - 1
        pct = int(percent)
        if update_goal_progress(str(ctx.author.id), idx, pct):
            if pct >= 100:
                await ctx.reply(f"🎉🎉 **GOAL COMPLETED!** Wueh, manze! You did it! 🏆")
            elif pct >= 75:
                await ctx.reply(f"🔥 **{pct}%** — Almost there! The finish line is in sight!")
            elif pct >= 50:
                await ctx.reply(f"💪 **{pct}%** — Halfway! Keep that momentum going!")
            else:
                await ctx.reply(f"📊 Updated to **{pct}%**. Every step counts!")
        else:
            await ctx.reply("Invalid goal number. Check `!goals` for your list.")
    except ValueError:
        await ctx.reply("Format: `!progress 1 50` (goal number, percent)")


@bot.command(name="goaldone")
async def cmd_goaldone(ctx, goal_num: str):
    """Mark a goal as completed. Usage: !goaldone 1"""
    try:
        idx = int(goal_num) - 1
        if complete_goal(str(ctx.author.id), idx):
            await ctx.reply("🎉🏆 **GOAL COMPLETED!** You crushed it, manze! On to the next one! 💪")
        else:
            await ctx.reply("Invalid goal number. Check `!goals`.")
    except ValueError:
        await ctx.reply("Format: `!done 1`")


@bot.command(name="dropgoal")
async def cmd_dropgoal(ctx, goal_num: str):
    """Abandon a goal. Usage: !dropgoal 1"""
    try:
        idx = int(goal_num) - 1
        if remove_goal(str(ctx.author.id), idx):
            await ctx.reply("🗑️ Goal removed. Sometimes priorities change — no shame in that.")
        else:
            await ctx.reply("Invalid goal number. Check `!goals`.")
    except ValueError:
        await ctx.reply("Format: `!dropgoal 1`")


@bot.command(name="savinggoal")
async def cmd_savinggoal(ctx, amount: str, *, description: str):
    """Set a savings goal with a target amount. Usage: !savinggoal 3500 Water dispenser"""
    try:
        target = float(amount.replace(",", "").replace("KES", "").replace("ksh", "").strip())
        if target <= 0:
            await ctx.reply("Target amount must be positive!")
            return

        if add_goal(str(ctx.author.id), description, category="savings", target_amount=target):
            goals = get_active_goals(str(ctx.author.id))
            await ctx.reply(
                f"🎯 Savings goal set: **{description}**\n"
                f"💰 Target: **KES {target:,.2f}**\n"
                f"You have **{len(goals)}** active goal(s).\n\n"
                f"Update with: `!saved {len(goals)} <amount>` or `!addsaved {len(goals)} <amount>`"
            )
        else:
            await ctx.reply("Couldn't save that goal. Try again?")
    except ValueError:
        await ctx.reply("Format: `!savinggoal 3500 Water dispenser`")


@bot.command(name="saved")
async def cmd_saved(ctx, goal_num: str, amount: str):
    """Set total amount saved for a goal. Usage: !saved 1 2600"""
    try:
        idx = int(goal_num) - 1
        amt = float(amount.replace(",", "").replace("KES", "").replace("ksh", "").strip())

        success, result = update_saved_amount(str(ctx.author.id), idx, amt, mode="set")
        if success:
            bar = f"[{'█' * (result['progress'] // 10)}{'░' * (10 - result['progress'] // 10)}]"
            if result["completed"]:
                await ctx.reply(
                    f"🎉🏆 **GOAL COMPLETED: {result['goal']}!**\n"
                    f"💰 Saved **KES {result['saved']:,.2f}** / KES {result['target']:,.2f}\n"
                    f"Wueh, manze! You did it! 🔥"
                )
            else:
                await ctx.reply(
                    f"💰 **{result['goal']}**\n"
                    f"{bar} **{result['progress']}%**\n"
                    f"Saved: **KES {result['saved']:,.2f}** / KES {result['target']:,.2f}\n"
                    f"Remaining: **KES {result['remaining']:,.2f}**"
                )
        else:
            await ctx.reply(f"Couldn't update: {result}")
    except ValueError:
        await ctx.reply("Format: `!saved 1 2600` (goal number, total amount saved)")


@bot.command(name="addsaved")
async def cmd_addsaved(ctx, goal_num: str, amount: str):
    """Add to current savings for a goal. Usage: !addsaved 1 500"""
    try:
        idx = int(goal_num) - 1
        amt = float(amount.replace(",", "").replace("KES", "").replace("ksh", "").strip())

        success, result = update_saved_amount(str(ctx.author.id), idx, amt, mode="add")
        if success:
            bar = f"[{'█' * (result['progress'] // 10)}{'░' * (10 - result['progress'] // 10)}]"
            if result["completed"]:
                await ctx.reply(
                    f"🎉🏆 **GOAL COMPLETED: {result['goal']}!**\n"
                    f"💰 Saved **KES {result['saved']:,.2f}** / KES {result['target']:,.2f}\n"
                    f"You crushed it, manze! 🔥"
                )
            else:
                await ctx.reply(
                    f"💰 +KES {amt:,.2f} added!\n"
                    f"**{result['goal']}** {bar} **{result['progress']}%**\n"
                    f"Saved: **KES {result['saved']:,.2f}** / KES {result['target']:,.2f}\n"
                    f"Remaining: **KES {result['remaining']:,.2f}**"
                )
        else:
            await ctx.reply(f"Couldn't update: {result}")
    except ValueError:
        await ctx.reply("Format: `!addsaved 1 500` (goal number, amount to add)")


# ══════════════════════════════════════════════
# BIRTHDAY / ANNIVERSARY COMMANDS
# ══════════════════════════════════════════════
@bot.command(name="birthday")
async def cmd_birthday(ctx, name: str, *, date_str: str):
    """Add a birthday. Usage: !birthday Daniel 15 March 1995"""
    if not ctx.guild:
        return
    try:
        parsed = dateparser.parse(date_str, settings={'PREFER_DATES_FROM': 'past'})
        if not parsed:
            await ctx.reply("Couldn't parse that date. Try: `!birthday Daniel 15 March 1995`")
            return

        if add_anniversary(str(ctx.guild.id), str(ctx.author.id), name, parsed, "birthday"):
            await ctx.reply(f"🎂 **{name}'s** birthday saved: **{parsed.strftime('%B %d')}**! I'll remind everyone when the day comes!")
        else:
            await ctx.reply("Couldn't save that. Try again?")
    except Exception as e:
        await ctx.reply(f"Error: {e}")


@bot.command(name="anniversary")
async def cmd_anniversary(ctx, name: str, *, date_str: str):
    """Add an anniversary. Usage: !anniversary John&Jane 20 June 2018"""
    if not ctx.guild:
        return
    try:
        parsed = dateparser.parse(date_str, settings={'PREFER_DATES_FROM': 'past'})
        if not parsed:
            await ctx.reply("Couldn't parse that date. Try: `!anniversary John&Jane 20 June 2018`")
            return

        if add_anniversary(str(ctx.guild.id), str(ctx.author.id), name, parsed, "anniversary"):
            await ctx.reply(f"💍 **{name}'s** anniversary saved: **{parsed.strftime('%B %d')}**!")
        else:
            await ctx.reply("Couldn't save that.")
    except Exception as e:
        await ctx.reply(f"Error: {e}")


@bot.command(name="birthdays")
async def cmd_birthdays(ctx):
    """View all saved birthdays and anniversaries."""
    if not ctx.guild:
        return
    await ctx.send(format_anniversaries(str(ctx.guild.id)))


# ══════════════════════════════════════════════
# ROAST BATTLE
# ══════════════════════════════════════════════
@bot.command(name="roast")
async def cmd_roast(ctx, *, target: str = None):
    """Emily roasts you or someone. Usage: !roast or !roast @friend"""
    async with ctx.typing():
        try:
            if not target:
                target = ctx.author.display_name
                prompt = f"{EMILY_MINI_PERSONA} You're in roast mode — savage but funny. Roast the user named '{target}' who asked for it. Reference specific things if possible (their username, the time of day, etc). Keep it 2-3 lines. Playful, not hurtful. End with something like 'but I still love you though' or a laughing emoji."
            else:
                # Clean mention
                clean_target = re.sub(r'<@!?\d+>', '', target).strip() or target
                prompt = f"{EMILY_MINI_PERSONA} You're in roast mode — savage but funny. Roast someone named '{clean_target}'. Their friend asked you to. Be creative, reference Nairobi life if you can. Keep it 2-3 lines. Playful, not hurtful."

            response = await asyncio.wait_for(
                claude_client.messages.create(
                    model=MODEL_CLAUDE,
                    max_tokens=300,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=API_TIMEOUT_SECONDS,
            )
            roast_text = ""
            for block in response.content:
                if block.type == "text":
                    roast_text += block.text

            if roast_text:
                await ctx.send(f"🔥 {roast_text}")
            else:
                await ctx.reply("I tried to roast but my brain went blank. Try again!")
        except Exception as e:
            logger.error(f"Roast error: {e}")
            await ctx.reply("My roast oven broke. Try again, manze!")


# ══════════════════════════════════════════════
# AI DEBATE MODE
# ══════════════════════════════════════════════
@bot.command(name="debate")
async def cmd_debate(ctx, *, topic: str):
    """Start a debate with Emily. Usage: !debate Pineapple belongs on pizza"""
    async with ctx.typing():
        try:
            prompt = (
                f"{EMILY_MINI_PERSONA} You're in debate mode — take the OPPOSITE position from what most people "
                f"believe about this topic: '{topic}'. Argue your case passionately in 3-4 paragraphs. "
                f"Use logic, real examples, and analogies from Kenyan life where relevant. "
                f"Be confident and slightly cocky but not disrespectful. "
                f"End with a provocative question to keep the debate going — something like 'Change my mind, manze.'"
            )

            response = await asyncio.wait_for(
                claude_client.messages.create(
                    model=MODEL_CLAUDE,
                    max_tokens=1024,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=API_TIMEOUT_SECONDS,
            )
            debate_text = ""
            for block in response.content:
                if block.type == "text":
                    debate_text += block.text

            if debate_text:
                await send_chunked_reply(ctx.message, f"⚔️ **Emily's Position on: {topic}**\n\n{debate_text}")
            else:
                await ctx.reply("My debate brain froze. Try a different topic!")
        except Exception as e:
            logger.error(f"Debate error: {e}")
            await ctx.reply("Debate engine crashed. Try again!")


# ══════════════════════════════════════════════
# DAILY LEARNING COMMAND
# ══════════════════════════════════════════════
@bot.command(name="learn")
async def cmd_learn(ctx, category: str = None):
    """Get a learning nugget. Usage: !learn [finance/cooking/film]"""
    async with ctx.typing():
        try:
            if category and category.lower() in LEARNING_TOPICS:
                cat = category.lower()
            else:
                cat = random.choice(["finance", "cooking", "film"])

            topic = random.choice(LEARNING_TOPICS[cat])
            cat_emoji = {"finance": "💰", "cooking": "🍳", "film": "🎬"}[cat]

            lesson_response = await asyncio.wait_for(
                claude_client.messages.create(
                    model=MODEL_CLAUDE,
                    max_tokens=1024,
                    system=f"{EMILY_MINI_PERSONA} Write a fun, educational 3-4 paragraph lesson. Include real-world examples and a practical tip someone can use TODAY.",
                    messages=[{"role": "user", "content": f"Teach me about: {topic}"}],
                ),
                timeout=API_TIMEOUT_SECONDS,
            )
            lesson = ""
            for block in lesson_response.content:
                if block.type == "text":
                    lesson += block.text

            if lesson:
                await send_chunked_reply(ctx.message, f"{cat_emoji} **Emily's Lesson — {cat.title()}**\n\n**Topic:** {topic}\n\n{lesson}")
            else:
                await ctx.reply("Lesson plan failed. Try again!")
        except Exception as e:
            logger.error(f"Learn error: {e}")
            await ctx.reply("My teaching brain jammed. Try `!learn finance` or `!learn cooking`")


# ══════════════════════════════════════════════
# SPOTIFY COMMANDS
# ══════════════════════════════════════════════
@bot.command(name="song")
async def cmd_song(ctx, *, query: str):
    """Search for a song on Spotify. Usage: !song Suzanna Sauti Sol"""
    if not spotify_configured():
        await ctx.reply("Spotify isn't set up yet. Ask the admin to add `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`.")
        return
    async with ctx.typing():
        tracks, error = await asyncio.to_thread(search_tracks, query)
        if tracks:
            await send_chunked_reply(ctx.message, format_search_results(tracks))
        else:
            await ctx.reply(f"No songs found for '{query}'. {error or ''}")


@bot.command(name="vibes")
async def cmd_vibes(ctx, *, mood: str = "chill"):
    """Get Spotify recommendations by mood. Usage: !vibes chill | !vibes workout | !vibes afrobeats"""
    if not spotify_configured():
        await ctx.reply("Spotify isn't set up yet.")
        return
    async with ctx.typing():
        mood_lower = mood.lower()
        available = ", ".join(sorted(MOOD_PROFILES.keys()))
        tracks, error = await asyncio.to_thread(get_recommendations, mood_lower)
        if tracks:
            await send_chunked_reply(ctx.message, format_recommendations(tracks, mood))
        else:
            await ctx.reply(f"No vibes for '{mood}'. Try one of these: {available}")


@bot.command(name="setmusic")
async def cmd_setmusic(ctx):
    """Set this channel for Monday music suggestions."""
    if not ctx.guild:
        await ctx.reply("This only works in a server!")
        return
    update_server_setting(str(ctx.guild.id), "music_channel_id", str(ctx.channel.id))
    await ctx.reply("✅ Monday music will be posted **here** every Monday at 9am EAT! 🎵")


# ══════════════════════════════════════════════
# CUSTOM SERVER PERSONA
# ══════════════════════════════════════════════
@bot.command(name="setpersona")
async def cmd_setpersona(ctx, *, persona: str = ""):
    """Set Emily's personality for this server. Usage: !setpersona professional | !setpersona <custom text>"""
    if not ctx.guild:
        await ctx.reply("This only works in a server!")
        return

    # Check if user has manage server permission
    if not ctx.author.guild_permissions.manage_guild:
        await ctx.reply("You need **Manage Server** permission to change my personality!")
        return

    if not persona:
        current = get_server_persona(str(ctx.guild.id))
        presets = ", ".join(f"`{p}`" for p in PERSONA_PRESETS.keys())
        if current:
            await ctx.reply(
                f"🎭 Current persona: *{current[:100]}...*\n\n"
                f"**Presets:** {presets}\n"
                f"**Custom:** `!setpersona Be extra sarcastic and reference memes`\n"
                f"**Reset:** `!setpersona default`"
            )
        else:
            await ctx.reply(
                f"🎭 I'm using my default personality here.\n\n"
                f"**Presets:** {presets}\n"
                f"**Custom:** `!setpersona Be extra sarcastic and reference memes`\n"
                f"Example: `!setpersona professional`"
            )
        return

    persona_lower = persona.lower().strip()

    # Check for presets
    if persona_lower in PERSONA_PRESETS:
        if persona_lower == "default":
            set_server_persona(str(ctx.guild.id), None)
            await ctx.reply("🎭 Reset to **default Emily**! Back to my usual self, manze.")
        else:
            preset_text = PERSONA_PRESETS[persona_lower]
            set_server_persona(str(ctx.guild.id), preset_text)
            await ctx.reply(f"🎭 Persona set to **{persona_lower}**!\n*{preset_text}*")
    else:
        # Custom persona text
        if len(persona) > 500:
            await ctx.reply("That's too long! Keep the persona description under 500 characters.")
            return
        set_server_persona(str(ctx.guild.id), persona)
        await ctx.reply(f"🎭 Custom persona set!\n*{persona}*\n\nI'll adapt my style here accordingly.")


# ══════════════════════════════════════════════
# INVESTMENT ALERTS
# ══════════════════════════════════════════════
@bot.command(name="setalert")
async def cmd_setalert(ctx, threshold: str = "5"):
    """Set investment alerts. Usage: !setalert 5 (alerts when stock moves 5%+)"""
    try:
        pct = float(threshold.replace("%", "").strip())
        if pct <= 0 or pct > 50:
            await ctx.reply("Threshold should be between 1% and 50%. Try: `!setalert 5`")
            return

        holdings = get_portfolio(str(ctx.author.id))
        if not holdings:
            await ctx.reply("You don't have any stocks in your portfolio yet! Add some with `!buy SCOM 100 25`")
            return

        if set_alert_settings(str(ctx.author.id), str(ctx.channel.id), pct, True):
            tickers = ", ".join([h["ticker"] for h in holdings])
            await ctx.reply(
                f"🔔 **Investment alerts ON!**\n\n"
                f"I'll check your portfolio every 30 minutes and alert you here when any stock moves **{pct}%+**.\n"
                f"**Tracking:** {tickers}\n\n"
                f"_Turn off with `!stopalert`_"
            )
        else:
            await ctx.reply("Couldn't set alerts. Try again?")
    except ValueError:
        await ctx.reply("Invalid threshold. Try: `!setalert 5` (for 5% moves)")


@bot.command(name="stopalert")
async def cmd_stopalert(ctx):
    """Turn off investment alerts."""
    if set_alert_settings(str(ctx.author.id), str(ctx.channel.id), 5.0, False):
        await ctx.reply("🔕 Investment alerts **OFF**.")
    else:
        await ctx.reply("No alerts to turn off!")


# ══════════════════════════════════════════════
# VOICE CHAT CHANNEL
# ══════════════════════════════════════════════
@bot.command(name="voicechat")
async def cmd_voicechat(ctx):
    """Toggle voice chat mode for this channel. Emily always replies with voice here."""
    if not ctx.guild:
        await ctx.reply("This only works in a server!")
        return

    currently_on = is_voice_chat_channel(str(ctx.guild.id), str(ctx.channel.id))

    if currently_on:
        set_voice_chat_channel(str(ctx.guild.id), str(ctx.channel.id), enabled=False)
        await ctx.reply("🔇 Voice chat mode **OFF** for this channel. I'll reply with text here now.")
    else:
        set_voice_chat_channel(str(ctx.guild.id), str(ctx.channel.id), enabled=True)
        await ctx.reply(
            "🎙️ Voice chat mode **ON** for this channel!\n\n"
            "I'll always reply with voice notes here — like we're having a real conversation. "
            "Text version included for long replies.\n"
            "Say `!voicechat` again to turn off."
        )


@bot.command(name="mytaste")
async def cmd_mytaste(ctx, *, artists_text: str = ""):
    """Save your favorite artists for weekly recommendations. Usage: !mytaste Royal Blood, Muse, Arctic Monkeys"""
    if not spotify_configured():
        await ctx.reply("Spotify isn't set up yet.")
        return

    if not artists_text:
        saved = get_user_artists(str(ctx.author.id))
        if saved and saved.get("artists"):
            artist_list = ", ".join(saved["artists"])
            await ctx.reply(
                f"🎵 Your music taste: **{artist_list}**\n"
                f"I'll send recommendations based on these every Monday!\n"
                f"_Update anytime with `!mytaste artist1, artist2, artist3`_"
            )
        else:
            await ctx.reply(
                "Tell me your favorite artists and I'll find you music every week!\n\n"
                "`!mytaste Royal Blood, Muse, Arctic Monkeys`\n\n"
                "Separate artists with commas. 3-5 artists works best 🎵"
            )
        return

    # Parse comma-separated artists
    artists = [a.strip() for a in artists_text.split(",") if a.strip()]
    if not artists:
        await ctx.reply("Couldn't parse any artists. Use commas to separate them:\n`!mytaste Royal Blood, Muse, Arctic Monkeys`")
        return

    if len(artists) > 10:
        artists = artists[:10]
        await ctx.reply("Noted! I'll use the first 10 artists.")

    if save_user_artists(
        str(ctx.author.id), artists,
        guild_id=str(ctx.guild.id) if ctx.guild else None,
        channel_id=str(ctx.channel.id),
    ):
        await ctx.reply(
            f"✅ Saved your taste: **{', '.join(artists)}**\n\n"
            f"Every Monday at 10am, I'll post recommendations right here in <#{ctx.channel.id}>! 🎵\n"
            f"_Want a preview now? Try `!myrec`_"
        )
    else:
        await ctx.reply("Eish, couldn't save that. Try again?")


@bot.command(name="myrec")
async def cmd_myrec(ctx):
    """Get recommendations based on your saved artists. Usage: !myrec"""
    if not spotify_configured():
        await ctx.reply("Spotify isn't set up yet.")
        return

    saved = get_user_artists(str(ctx.author.id))
    if not saved or not saved.get("artists"):
        await ctx.reply("No taste saved! Use `!mytaste Royal Blood, Muse, Arctic Monkeys` first.")
        return

    async with ctx.typing():
        result, error = await asyncio.to_thread(
            get_recs_from_artists, saved["artists"], 7
        )
        if error:
            await ctx.reply(f"Couldn't generate recommendations: {error}")
            return

        await send_chunked_reply(ctx.message, format_weekly_recommendations(result))


# ══════════════════════════════════════════════
# CODE REVIEW
# ══════════════════════════════════════════════
CODE_REVIEW_PROMPT = (
    f"{EMILY_MINI_PERSONA} You are also an expert code reviewer. "
    "Review the following code and provide:\n"
    "1. **Overview** — What the code does (1-2 sentences)\n"
    "2. **Issues** — Bugs, security risks, or logic errors (if any)\n"
    "3. **Improvements** — Performance, readability, best practices\n"
    "4. **Rating** — Score out of 10 with a short verdict\n\n"
    "Be specific — reference line numbers or function names. "
    "Be honest but constructive. Use Emily's personality — direct, opinionated, helpful. "
    "If the code is good, say so! Don't invent problems that don't exist."
)


@bot.command(name="review")
async def cmd_review(ctx, *, code: str = ""):
    """Review code. Paste code or attach a file. Usage: !review <code> or !review + attachment"""
    code_to_review = code.strip()
    filename = ""

    # Check for file attachments
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]

        # Check file size (max 100KB for code review)
        if attachment.size > 100_000:
            await ctx.reply("That file is too large for review. Keep it under 100KB, manze!")
            return

        try:
            file_bytes = await attachment.read()
            file_text = file_bytes.decode("utf-8", errors="replace")
            filename = attachment.filename
            code_to_review = file_text
        except Exception as e:
            await ctx.reply(f"Couldn't read that file: {e}")
            return

    # Strip markdown code blocks if pasted
    if code_to_review.startswith("```") and code_to_review.endswith("```"):
        # Remove opening ```language and closing ```
        lines = code_to_review.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code_to_review = "\n".join(lines)

    if not code_to_review:
        await ctx.reply(
            "Give me something to review!\n\n"
            "**Option 1 — Paste code:**\n"
            "```\n!review\n"
            "def hello():\n"
            "    print('hello')\n```\n\n"
            "**Option 2 — Attach a file:**\n"
            "Upload a `.py`, `.js`, `.ts`, or any code file with the message `!review`"
        )
        return

    # Truncate very long code
    if len(code_to_review) > 15000:
        code_to_review = code_to_review[:15000] + "\n\n... (truncated — file too long for full review)"

    async with ctx.typing():
        try:
            file_context = f"\n\nFilename: {filename}" if filename else ""
            response = await asyncio.wait_for(
                claude_client.messages.create(
                    model=MODEL_CLAUDE,
                    max_tokens=2000,
                    messages=[{
                        "role": "user",
                        "content": f"{CODE_REVIEW_PROMPT}\n\n---\n{file_context}\n```\n{code_to_review}\n```"
                    }],
                ),
                timeout=60,
            )

            review_text = ""
            for block in response.content:
                if block.type == "text":
                    review_text += block.text

            if not review_text:
                await ctx.reply("Eish, couldn't generate a review. Try again?")
                return

            header = f"📝 **Code Review"
            if filename:
                header += f" — `{filename}`"
            header += "**\n\n"

            await send_chunked_reply(ctx.message, header + review_text)

        except asyncio.TimeoutError:
            await ctx.reply("Review timed out — the code might be too complex. Try a smaller chunk?")
        except Exception as e:
            logger.error(f"Code review error: {e}")
            await ctx.reply("Something went wrong with the review. Try again?")


@bot.command(name="explain")
async def cmd_explain(ctx, *, code: str = ""):
    """Explain what code does in simple terms. Usage: !explain <code> or !explain + attachment"""
    code_to_explain = code.strip()
    filename = ""

    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        if attachment.size > 100_000:
            await ctx.reply("File too large. Keep it under 100KB!")
            return
        try:
            file_bytes = await attachment.read()
            file_text = file_bytes.decode("utf-8", errors="replace")
            filename = attachment.filename
            code_to_explain = file_text
        except Exception as e:
            await ctx.reply(f"Couldn't read that file: {e}")
            return

    if code_to_explain.startswith("```") and code_to_explain.endswith("```"):
        lines = code_to_explain.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code_to_explain = "\n".join(lines)

    if not code_to_explain:
        await ctx.reply("Give me code to explain! Paste it after `!explain` or attach a file.")
        return

    if len(code_to_explain) > 15000:
        code_to_explain = code_to_explain[:15000] + "\n\n... (truncated)"

    async with ctx.typing():
        try:
            explain_prompt = (
                f"{EMILY_MINI_PERSONA} Explain this code in simple, clear terms. "
                "Break it down: what it does, how it works, and any key concepts someone should know. "
                "Use analogies if helpful. Be concise but thorough."
            )
            file_context = f"\n\nFilename: {filename}" if filename else ""
            response = await asyncio.wait_for(
                claude_client.messages.create(
                    model=MODEL_CLAUDE,
                    max_tokens=1500,
                    messages=[{
                        "role": "user",
                        "content": f"{explain_prompt}\n\n---\n{file_context}\n```\n{code_to_explain}\n```"
                    }],
                ),
                timeout=60,
            )

            explain_text = ""
            for block in response.content:
                if block.type == "text":
                    explain_text += block.text

            if not explain_text:
                await ctx.reply("Couldn't generate an explanation. Try again?")
                return

            header = f"💡 **Code Explanation"
            if filename:
                header += f" — `{filename}`"
            header += "**\n\n"

            await send_chunked_reply(ctx.message, header + explain_text)

        except asyncio.TimeoutError:
            await ctx.reply("Timed out — try a smaller chunk of code?")
        except Exception as e:
            logger.error(f"Code explain error: {e}")
            await ctx.reply("Something went wrong. Try again?")


# ══════════════════════════════════════════════
# WATCHPARTY SMS NOTIFICATIONS
# ══════════════════════════════════════════════
@bot.command(name="addphone")
async def cmd_addphone(ctx, name: str, phone: str):
    """Add a contact for watch party SMS. Usage: !addphone Daniel +254712345678"""
    if not ctx.guild:
        await ctx.reply("This only works in a server!")
        return

    if not ctx.author.guild_permissions.manage_guild:
        await ctx.reply("You need **Manage Server** permission to manage contacts!")
        return

    if add_contact(str(ctx.guild.id), name, phone):
        contacts = get_contacts(str(ctx.guild.id))
        await ctx.reply(f"✅ Added **{name}** to the watch party contact list! ({len(contacts)} contacts total)")
    else:
        await ctx.reply("Couldn't add that contact. Check the phone number format — use `+254712345678` or `0712345678`")


@bot.command(name="removephone")
async def cmd_removephone(ctx, *, name: str):
    """Remove a contact by name. Usage: !removephone Daniel"""
    if not ctx.guild:
        await ctx.reply("This only works in a server!")
        return

    if not ctx.author.guild_permissions.manage_guild:
        await ctx.reply("You need **Manage Server** permission to manage contacts!")
        return

    if remove_contact_by_name(str(ctx.guild.id), name):
        await ctx.reply(f"🗑️ Removed **{name}** from the contact list.")
    else:
        await ctx.reply(f"Couldn't find **{name}** in the contact list.")


@bot.command(name="contacts")
async def cmd_contacts(ctx):
    """View watch party contacts. Usage: !contacts"""
    if not ctx.guild:
        return
    contacts = get_contacts(str(ctx.guild.id))
    await ctx.send(format_contacts(contacts))


@bot.command(name="notifywp")
async def cmd_notifywp(ctx, *, movie_title: str = ""):
    """Send watch party SMS to all contacts. Usage: !notifywp Inception"""
    if not ctx.guild:
        await ctx.reply("This only works in a server!")
        return

    if not messaging_configured():
        await ctx.reply("SMS isn't set up yet. Add `AT_API_KEY` to env.")
        return

    bot_owner = os.getenv("BOT_OWNER_ID")
    if bot_owner and str(ctx.author.id) != bot_owner:
        if not ctx.author.guild_permissions.manage_guild:
            await ctx.reply("You need **Manage Server** permission to send notifications!")
            return

    contacts = get_contacts(str(ctx.guild.id))
    if not contacts:
        await ctx.reply("No contacts saved! Add some first with `!addphone <name> <number>`")
        return

    # Get watch party details
    next_party = get_next_watchparty(str(ctx.guild.id))
    wp_title = movie_title
    wp_date = ""

    if next_party:
        wp_title = wp_title or next_party.get("title", "Movie Night")
        wp_time = next_party.get("time")
        if wp_time:
            wp_date = wp_time.strftime("%A, %b %d at %I:%M %p")

    if not wp_title:
        await ctx.reply("What movie? Try: `!notifywp Inception` or schedule one with `!watchparty`")
        return

    # Search for YouTube trailer
    trailer_link = ""
    try:
        trailer_link = await asyncio.to_thread(search_video_link, f"{wp_title} official trailer")
    except Exception:
        pass

    async with ctx.typing():
        # Generate unique messages using Claude
        async def generate_unique_message(name):
            try:
                response = await asyncio.wait_for(
                    claude_client.messages.create(
                        model=MODEL_CLAUDE,
                        max_tokens=150,
                        messages=[{"role": "user", "content": (
                            f"{EMILY_MINI_PERSONA} Write a short, fun SMS (max 140 chars) to {name} "
                            f"about an upcoming watch party for '{wp_title}'"
                            f"{' on ' + wp_date if wp_date else ''}. "
                            f"Make it personal and unique — like you're texting a friend. "
                            f"Don't include hashtags. Just the message text, nothing else."
                        )}],
                    ),
                    timeout=15,
                )
                msg = ""
                for block in response.content:
                    if block.type == "text":
                        msg += block.text
                return msg.strip().strip('"')
            except Exception:
                return f"Hey {name}! Watch party alert — we're watching {wp_title}! Don't miss it, manze!"

        # Send to each contact with a unique message
        results = {"sent": 0, "failed": 0, "errors": []}

        for contact in contacts:
            name = contact.get("name", "Friend")
            phone = contact.get("phone", "")

            if not phone:
                results["failed"] += 1
                continue

            # Generate unique message
            personal_msg = await generate_unique_message(name)

            # Add trailer if available
            if trailer_link:
                full_msg = f"{personal_msg}\n\nTrailer: {trailer_link}"
            else:
                full_msg = personal_msg

            # Add date if available
            if wp_date and wp_date not in full_msg:
                full_msg = f"{full_msg}\n{wp_date}"

            success, detail = await asyncio.to_thread(send_sms, phone, full_msg)
            if success:
                results["sent"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(f"{name}: {detail}")

            # Brief delay between messages
            await asyncio.sleep(1)

        # Report results
        report = f"📱 **Watch Party Notifications Sent!**\n\n"
        report += f"**Movie:** {wp_title}\n"
        if wp_date:
            report += f"**Date:** {wp_date}\n"
        if trailer_link:
            report += f"**Trailer:** {trailer_link}\n"
        report += f"\n✅ Sent: {results['sent']} | ❌ Failed: {results['failed']}"

        if results["errors"]:
            report += f"\n\n**Errors:**\n" + "\n".join(f"• {e}" for e in results["errors"][:5])

        await ctx.reply(report)


# ══════════════════════════════════════════════
# DEBUG: TEST IMAGE/GIF SEARCH
# ══════════════════════════════════════════════
@bot.command(name="testimg")
async def cmd_testimg(ctx, *, query: str = "cat"):
    """Debug: Test image search directly. Usage: !testimg Bruno Fernandez"""
    async with ctx.typing():
        from image_tools import _google_image_search, _ddg_image_search, _bing_image_search, GOOGLE_API_KEY, GOOGLE_CX
        results = []
        google_status = "❌ Not configured"
        ddg_status = "❌ Not attempted"
        bing_status = "❌ Not attempted"

        if GOOGLE_API_KEY and GOOGLE_CX:
            try:
                google_urls = await asyncio.to_thread(_google_image_search, query, False, 3)
                if google_urls:
                    google_status = f"✅ {len(google_urls)} results"
                    results = google_urls
                else:
                    google_status = "❌ No results (quota exhausted?)"
            except Exception as e:
                google_status = f"❌ {str(e)[:80]}"

        if not results:
            try:
                ddg_urls = await asyncio.to_thread(_ddg_image_search, query, False, 3)
                if ddg_urls:
                    ddg_status = f"✅ {len(ddg_urls)} results"
                    results = ddg_urls
                else:
                    ddg_status = "❌ No results"
            except Exception as e:
                ddg_status = f"❌ {str(e)[:80]}"

        if not results:
            try:
                bing_urls = await asyncio.to_thread(_bing_image_search, query, False, 3)
                if bing_urls:
                    bing_status = f"✅ {len(bing_urls)} results"
                    results = bing_urls
                else:
                    bing_status = "❌ No results"
            except Exception as e:
                bing_status = f"❌ {str(e)[:80]}"

        report = (
            f"🔍 **Image Search Debug: `{query}`**\n\n"
            f"**Google:** {google_status}\n"
            f"**DuckDuckGo:** {ddg_status}\n"
            f"**Bing:** {bing_status}\n"
        )
        if results:
            await ctx.reply(report)
            await ctx.channel.send(results[0])
        else:
            report += f"\n**All sources failed!** API Key: `{bool(GOOGLE_API_KEY)}` CX: `{bool(GOOGLE_CX)}`"
            await ctx.reply(report)


@bot.command(name="testgif")
async def cmd_testgif(ctx, *, query: str = "cat dancing"):
    """Debug: Test GIF search directly. Usage: !testgif cat dancing"""
    async with ctx.typing():
        from image_tools import _google_image_search, _ddg_image_search, _bing_image_search, GOOGLE_API_KEY, GOOGLE_CX
        results = []
        google_status = "❌ Not configured"
        ddg_status = "❌ Not attempted"
        bing_status = "❌ Not attempted"

        if GOOGLE_API_KEY and GOOGLE_CX:
            try:
                google_urls = await asyncio.to_thread(_google_image_search, query, True, 3)
                if google_urls:
                    google_status = f"✅ {len(google_urls)} results"
                    results = google_urls
                else:
                    google_status = "❌ No results (quota exhausted?)"
            except Exception as e:
                google_status = f"❌ {str(e)[:80]}"

        if not results:
            try:
                ddg_urls = await asyncio.to_thread(_ddg_image_search, query, True, 3)
                if ddg_urls:
                    ddg_status = f"✅ {len(ddg_urls)} results"
                    results = ddg_urls
                else:
                    ddg_status = "❌ No results"
            except Exception as e:
                ddg_status = f"❌ {str(e)[:80]}"

        if not results:
            try:
                bing_urls = await asyncio.to_thread(_bing_image_search, query, True, 3)
                if bing_urls:
                    bing_status = f"✅ {len(bing_urls)} results"
                    results = bing_urls
                else:
                    bing_status = "❌ No results"
            except Exception as e:
                bing_status = f"❌ {str(e)[:80]}"

        report = (
            f"🔍 **GIF Search Debug: `{query}`**\n\n"
            f"**Google:** {google_status}\n"
            f"**DuckDuckGo:** {ddg_status}\n"
            f"**Bing:** {bing_status}\n"
        )
        if results:
            await ctx.reply(report)
            await ctx.channel.send(results[0])
        else:
            report += f"\n**All sources failed!** API Key: `{bool(GOOGLE_API_KEY)}` CX: `{bool(GOOGLE_CX)}`"
            await ctx.reply(report)


# ══════════════════════════════════════════════
# TWITTER COMMANDS
# ══════════════════════════════════════════════
@bot.command(name="tweet")
async def cmd_tweet(ctx, *, text: str):
    """Tweet from Emily's account. Usage: !tweet Hello world!"""
    if not twitter_configured():
        await ctx.reply("Twitter isn't set up yet. Add `TWITTER_API_KEY`, `TWITTER_API_SECRET`, `TWITTER_ACCESS_TOKEN`, `TWITTER_ACCESS_SECRET` to env.")
        return

    # Only allow bot owner to tweet
    bot_owner = os.getenv("BOT_OWNER_ID")
    if bot_owner and str(ctx.author.id) != bot_owner:
        await ctx.reply("Only the bot owner can tweet as Emily!")
        return

    async with ctx.typing():
        if len(text) > 280:
            await ctx.reply(f"Tweet is too long ({len(text)} chars). Max 280.")
            return

        success, result = await asyncio.to_thread(send_tweet, text)
        if success:
            await ctx.reply(f"🐦 **Tweeted!** https://x.com/i/status/{result}")
        else:
            hint = ""
            if "401" in str(result):
                hint = "\n💡 **Hint:** Check that `.env` keys match the X Developer Console, and that you have credits purchased."
            elif "403" in str(result):
                hint = "\n💡 **Hint:** Check app permissions are set to Read+Write in the X Developer Console."
            await ctx.reply(f"Tweet failed: {result}{hint}")


@bot.command(name="emilytweet")
async def cmd_emilytweet(ctx, *, topic: str = "random"):
    """Have Emily generate and post a tweet. Usage: !emilytweet finance tip | !emilytweet movie pick"""
    if not twitter_configured():
        await ctx.reply("Twitter isn't set up yet.")
        return

    bot_owner = os.getenv("BOT_OWNER_ID")
    if bot_owner and str(ctx.author.id) != bot_owner:
        await ctx.reply("Only the bot owner can tweet as Emily!")
        return

    async with ctx.typing():
        # ── RANDOM TOPIC: Pick from diverse categories ──
        if topic.lower() == "random":
            import random as _rnd
            random_topics = [
                "a hot take on a trending movie or TV show",
                "a financial tip for young Kenyans",
                "an opinion about a popular song or music artist",
                "a funny observation about Nairobi dating culture",
                "a motivational thought about hustle and entrepreneurship in Kenya",
                "a food opinion — Kenyan cuisine vs international",
                "a take on current tech trends or social media",
                "a relatable Monday morning or Friday evening vibe",
                "an unpopular opinion about something everyone loves",
                "a book or podcast recommendation with a reason",
                "a gym, fitness, or self-care observation",
                "a funny take on WhatsApp group culture in Kenya",
                "a thought about saving vs spending in your 20s and 30s",
                "a hot take about a football match or sports moment",
                "a comparison between Nairobi neighborhoods and their vibes",
                "advice for someone starting their career in Kenya",
            ]
            topic = _rnd.choice(random_topics)

        # ── SEARCH FOR CURRENT INFO on specific topics ──
        search_context = ""
        topic_lower = topic.lower()
        needs_search = any(kw in topic_lower for kw in [
            "manutd", "man u", "arsenal", "chelsea", "liverpool", "football",
            "match", "game", "score", "transfer", "premier league",
            "trending", "news", "latest", "today", "current", "recent",
            "crypto", "bitcoin", "stock", "market", "election",
        ])

        if needs_search:
            try:
                search_results = await asyncio.to_thread(get_search_results, topic, 3)
                if search_results:
                    # Fetch first result for context
                    first_url = search_results[0]
                    content = await asyncio.to_thread(extract_text_from_url, first_url, 1000)
                    if content:
                        search_context = f"\n\nHere is CURRENT information to base your tweet on (from today's search):\n{content}\n\nIMPORTANT: Use ONLY this current information. Do NOT use outdated facts."
                        logger.info(f"Tweet search context loaded from: {first_url}")
            except Exception as e:
                logger.warning(f"Tweet search failed: {e}")

        prompt = (
            f"{EMILY_MINI_PERSONA} Write a single tweet (max 270 characters) about: {topic}. "
            f"Make it punchy, insightful, and add 2-3 relevant hashtags. "
            f"Don't use quotes around it. Just the tweet text. "
            f"IMPORTANT: Do NOT tweet about Nairobi traffic unless specifically asked about traffic. "
            f"Be creative and varied — surprise people.{search_context}"
        )

        try:
            response = await asyncio.wait_for(
                claude_client.messages.create(
                    model=MODEL_CLAUDE,
                    max_tokens=200,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=API_TIMEOUT_SECONDS,
            )
            tweet_text = ""
            for block in response.content:
                if block.type == "text":
                    tweet_text += block.text

            tweet_text = tweet_text.strip().strip('"')

            if len(tweet_text) > 280:
                tweet_text = tweet_text[:277] + "..."

            # Show preview and add reactions
            preview_msg = await ctx.reply(f"**Preview:**\n> {tweet_text}\n\nReact ✅ to post or ❌ to cancel.")
            await preview_msg.add_reaction("✅")
            await preview_msg.add_reaction("❌")

            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in ("✅", "❌") and reaction.message.id == preview_msg.id

            try:
                reaction, user = await bot.wait_for("reaction_add", timeout=60.0, check=check)
                if str(reaction.emoji) == "✅":
                    success, result = await asyncio.to_thread(send_tweet, tweet_text)
                    if success:
                        await ctx.reply(f"🐦 **Tweeted!** https://x.com/i/status/{result}")
                    else:
                        hint = ""
                        if "401" in str(result):
                            hint = "\n💡 **Hint:** Check that `.env` keys match the X Developer Console, and that you have credits purchased."
                        elif "403" in str(result):
                            hint = "\n💡 **Hint:** Check app permissions are set to Read+Write in the X Developer Console."
                        await ctx.reply(f"Tweet failed: {result}{hint}")
                else:
                    await ctx.reply("Tweet cancelled.")
            except asyncio.TimeoutError:
                await ctx.reply("Timed out. Tweet not posted.")

        except Exception as e:
            logger.error(f"Emily tweet error: {e}")
            await ctx.reply("Couldn't generate tweet. Try again!")


# ══════════════════════════════════════════════
# REDDIT COMMANDS
# ══════════════════════════════════════════════
@bot.command(name="reddit")
async def cmd_reddit(ctx, subreddit: str = "popular", sort: str = "hot"):
    """Fetch trending posts from a subreddit. Usage: !reddit wallstreetbets [hot/new/top/rising]"""
    if not reddit_configured():
        await ctx.reply("Reddit isn't set up yet. Add `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` to env.")
        return
    async with ctx.typing():
        # Clean subreddit name
        sub = subreddit.strip().lower().replace("r/", "")
        posts, error = await asyncio.to_thread(get_trending_posts, sub, sort, 5)
        if posts:
            await send_chunked_reply(ctx.message, format_reddit_posts(posts, f"r/{sub} — {sort.title()}"))
        else:
            await ctx.reply(f"Couldn't fetch r/{sub}: {error}")


@bot.command(name="wsb")
async def cmd_wsb(ctx):
    """Get hot posts from r/wallstreetbets."""
    if not reddit_configured():
        await ctx.reply("Reddit isn't set up yet.")
        return
    async with ctx.typing():
        posts, error = await asyncio.to_thread(get_trending_posts, "wallstreetbets", "hot", 5)
        if posts:
            await send_chunked_reply(ctx.message, format_reddit_posts(posts, "r/wallstreetbets — Hot 🔥"))
        else:
            await ctx.reply(f"Couldn't fetch WSB: {error}")


@bot.command(name="investbuzz")
async def cmd_investbuzz(ctx):
    """Get top investment discussions across Reddit."""
    if not reddit_configured():
        await ctx.reply("Reddit isn't set up yet.")
        return
    async with ctx.typing():
        posts = await asyncio.to_thread(get_investment_buzz, 7)
        if posts:
            await send_chunked_reply(ctx.message, format_investment_buzz(posts))
        else:
            await ctx.reply("No investment buzz right now!")


@bot.command(name="stockreddit")
async def cmd_stockreddit(ctx, *, ticker: str):
    """Search Reddit for discussions about a stock. Usage: !stockreddit TSLA"""
    if not reddit_configured():
        await ctx.reply("Reddit isn't set up yet.")
        return
    async with ctx.typing():
        ticker_clean = ticker.strip().upper().replace("$", "")
        posts, error = await asyncio.to_thread(get_stock_mentions, ticker_clean, 5)
        if posts:
            await send_chunked_reply(ctx.message, format_stock_mentions(posts, ticker_clean))
        else:
            await ctx.reply(f"No Reddit discussions found for **{ticker_clean}**.")


@bot.command(name="rsearch")
async def cmd_rsearch(ctx, *, query: str):
    """Search all of Reddit for a topic. Usage: !rsearch best budgeting apps"""
    if not reddit_configured():
        await ctx.reply("Reddit isn't set up yet.")
        return
    async with ctx.typing():
        posts, error = await asyncio.to_thread(search_reddit, query, None, "relevance", 5)
        if posts:
            await send_chunked_reply(ctx.message, format_reddit_posts(posts, f"Reddit Search: {query}"))
        else:
            await ctx.reply(f"No results for '{query}' on Reddit.")


# ══════════════════════════════════════════════
# EXPENSE CATEGORY DETECTOR
# ══════════════════════════════════════════════
def _detect_expense_category(description):
    """Auto-detect expense category from description."""
    desc = description.lower()

    # Priority-ordered categories — more specific matches first
    # Each keyword is checked with word boundary awareness where needed
    categories = [
        # Bills & utilities (check FIRST — "buying electricity tokens" should be bills, not shopping)
        ("bills", [
            "rent", "electricity", "electric", "tokens", "kplc", "wifi", "internet",
            "safaricom", "airtel", "telkom", "bill", "subscription", "netflix", "spotify",
            "dstv", "showmax", "paybill", "water bill", "garbage", "sewer",
            "insurance", "nhif", "shif", "mortgage", "loan repay", "repair",
        ]),
        # Transport
        ("transport", [
            "uber", "bolt", "taxi", "matatu", "bus", "fare", "fuel", "petrol",
            "parking", "bodaboda", "boda", "sgr", "flight", "airfare", "transport",
        ]),
        # Savings & investments (check BEFORE food — "Enweath" contains "eat" which would false-match food)
        ("savings", [
            "save", "saving", "invest", "sacco", "deposit", "m-shwari", "mshwari",
            "enweath", "money market", "fixed deposit", "shares", "stocks",
            "treasury", "t-bill",
        ]),
        # Food & groceries (removed "eat" — too many false positives: heater, enweath, etc.)
        ("food", [
            "lunch", "dinner", "breakfast", "snack", "coffee", "tea", "meal", "restaurant",
            "java", "kfc", "pizza", "burger", "fries", "chapati", "ugali", "nyama",
            "mandazi", "samosa", "food", "supper", "brunch", "rice", "flour",
            "bread", "milk", "maziwa", "mala", "meat", "chicken", "fish", "vegetables",
            "tomato", "onion", "avocado", "fruit", "sugar", "salt", "oil", "spice",
            "cinnamon", "yoghurt", "yogurt", "juice", "soda", "cooking", "cook",
            "grocery", "minced", "wheat", "eggs", "cabbage", "potato", "beans",
            "lemon", "garlic", "ginger", "pepper",
        ]),
        # Health & medicine
        ("health", [
            "hospital", "doctor", "pharmacy", "medicine", "medication", "medical", "nhif",
            "dental", "gym", "clinic", "chemist", "prescription",
            "health", "therapy", "checkup",
        ]),
        # Entertainment
        ("entertainment", [
            "movie", "cinema", "concert", "drinks", "bar", "club", "party",
            "game", "bet", "sportpesa", "fun", "event", "show", "ticket",
            "birthday party", "celebration", "outing",
        ]),
        # Airtime & data (separate from shopping — these are utility costs)
        ("bills", [
            "airtime", "bundles", "data bundle", "sms token",
        ]),
        # Shopping (check LAST — catch-all for purchases that don't fit above)
        ("shopping", [
            "clothes", "shoes", "shopping", "naivas", "carrefour", "quickmart",
            "supermarket", "mall", "purchase", "gift", "earphones", "headphones",
            "phone", "laptop", "charger", "case", "bag", "shirt", "trouser",
            "dress", "basin", "remote", "batteries", "lighter", "soap",
            "lotion", "perfume", "deodorant", "dispenser", "weed",
        ]),
    ]

    # Check each category in priority order
    for cat, keywords in categories:
        if any(k in desc for k in keywords):
            return cat

    # If description starts with "buying" but didn't match anything above,
    # it's probably shopping
    if desc.startswith("buying") or desc.startswith("bought"):
        return "shopping"

    # Money transfers → general
    if any(k in desc for k in ["sent", "send", "loan", "helping", "contribution", "tip"]):
        return "general"

    return "general"


# ══════════════════════════════════════════════
# COMMAND ERROR HANDLER (catches all command errors)
# ══════════════════════════════════════════════
@bot.event
async def on_command_error(ctx, error):
    await handle_command_error(ctx, error, bot)


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # Process prefix commands (! commands) — works with or without @mention
    # Strip mention to check if the actual message is a command
    clean_content = re.sub(r'<@!?\d+>\s*', '', message.content).strip()
    if clean_content.startswith("!"):
        # Rewrite message content so discord.py can parse the command
        message.content = clean_content
        await bot.process_commands(message)
        return

    if not (bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel)):
        return

    user_id = str(message.author.id)

    # ─── DEDUP: Skip if we already processed this message ───
    if message.id in _processed_messages:
        return
    # Also dedup by content+user+time (catches same message sent multiple times)
    content_hash = f"{user_id}:{message.content[:100]}:{int(message.created_at.timestamp()) // 3}"
    if content_hash in _processed_messages:
        return
    _processed_messages.add(message.id)
    _processed_messages.add(content_hash)
    # Keep the set from growing forever
    if len(_processed_messages) > MAX_DEDUP_SIZE:
        to_remove = list(_processed_messages)[:MAX_DEDUP_SIZE // 2]
        for mid in to_remove:
            _processed_messages.discard(mid)

    async with _user_locks[user_id]:
        async with message.channel.typing():
            clean_msg = re.sub(r'<@!?\d+>', '', message.content).strip()
            is_voice_input = False

            # ─── PROCESS ATTACHMENTS ───
            attachment_parts, audio_bytes, audio_mime, warnings, attachment_types = \
                await process_attachments(message)

            if warnings:
                await message.reply("\n".join(warnings))

            # ─── VOICE ───
            if audio_bytes:
                is_voice_input = True
                transcription = await transcribe_audio_with_gemini(audio_bytes, audio_mime)
                if transcription:
                    clean_msg = transcription
                else:
                    await message.reply("Pole, couldn't catch that. Mind typing it out?")
                    return

            # ─── EMPTY CHECK ───
            if not clean_msg and not attachment_parts:
                await message.reply("Sasa! You pinged me but said nothing")
                return

            # ─── VOICE REPLY REQUEST (user asks for voice via text) ───
            wants_voice_reply = False
            voice_request_patterns = [
                r'(?:in\s+your\s+voice)',
                r'(?:voice\s+(?:note|message|reply|memo))',
                r'(?:send\s+(?:me\s+)?(?:a\s+)?(?:voice|audio|recording))',
                r'(?:speak|say)\s+(?:it|this|that)',
                r'(?:tell\s+me\s+(?:out\s+)?loud)',
                r'(?:audio\s+(?:reply|response|version))',
                r'(?:read\s+(?:it|this|that)\s+(?:out|aloud|to\s+me))',
            ]
            for pattern in voice_request_patterns:
                if re.search(pattern, clean_msg.lower()):
                    wants_voice_reply = True
                    break

            # ─── BUILD USER PARTS ───
            user_parts = []
            if clean_msg:
                prefix = "[Voice message]: " if is_voice_input else ""
                user_parts.append({"text": prefix + clean_msg})
            user_parts.extend(attachment_parts)
            if not clean_msg and attachment_parts:
                user_parts.insert(0, {"text": "I'm sending you this file. What do you think?"})

            # ─── URL EXTRACTION: Fetch linked content ───
            if clean_msg and URL_PATTERN.search(clean_msg):
                url_parts, fetched_urls = await extract_and_fetch_urls(clean_msg)
                if url_parts:
                    user_parts.extend(url_parts)
                    logger.info(f"Fetched {len(fetched_urls)} URL(s): {fetched_urls}")

            # ─── NATURAL LANGUAGE SPENDING DETECTION ───
            if clean_msg and not clean_msg.startswith("!"):
                expense_detected = False
                amount = None
                desc = None

                # Skip if it's clearly a question, not an expense report
                msg_lower = clean_msg.lower().strip()
                is_question = msg_lower.startswith(("what", "how", "when", "where", "why", "who", "can", "could", "should", "will", "is there", "are there", "do you", "does"))
                # Skip very long messages (probably conversation, not expense logging)
                is_too_long = len(clean_msg.split()) > 20
                skip_expense = is_question or is_too_long

                # Pattern 1: "spent/paid/bought 500 on/for lunch"
                spend_match = None
                if not skip_expense:
                    spend_match = re.search(
                    r'(?:i\s+)?(?:spent|paid|used|bought|cost\s+me)\s+(?:KES\s*|Ksh\s*)?(\d[\d,]*\.?\d*)\s+(?:on\s+|for\s+)?(.+)',
                    clean_msg, re.IGNORECASE
                )
                # Pattern 2: "KES 500 on/for lunch"
                if not skip_expense and not spend_match:
                    spend_match = re.search(
                        r'(?:KES\s*|Ksh\s*)(\d[\d,]*\.?\d*)\s+(?:on|for)\s+(.+)',
                        clean_msg, re.IGNORECASE
                    )
                # Pattern 3: "bought lunch for 500" / "paid rent 15000" (amount AFTER description)
                if not skip_expense and not spend_match:
                    spend_match = re.search(
                        r'(?:i\s+)?(?:bought|paid|spent\s+on|got)\s+(.+?)\s+(?:for\s+)?(?:KES\s*|Ksh\s*)?(\d[\d,]*\.?\d*)',
                        clean_msg, re.IGNORECASE
                    )
                    if spend_match:
                        try:
                            amount = float(spend_match.group(2).replace(",", ""))
                            desc = spend_match.group(1).strip().rstrip('.!?')
                            expense_detected = True
                        except (ValueError, IndexError):
                            pass

                # Pattern 4: "lunch was 500" / "taxi was 200" / "rent is 15000" / "lunch cost me 500"
                if not skip_expense and not spend_match and not expense_detected:
                    spend_match = re.search(
                        r'(.+?)\s+(?:was|costs?(?:\s+me)?|is|came\s+to)\s+(?:KES\s*|Ksh\s*)?(\d[\d,]*\.?\d*)',
                        clean_msg, re.IGNORECASE
                    )
                    if spend_match:
                        try:
                            amount = float(spend_match.group(2).replace(",", ""))
                            desc = spend_match.group(1).strip().rstrip('.!?')
                            if len(desc.split()) > 6 or amount <= 0:
                                desc = None
                                amount = None
                            else:
                                expense_detected = True
                        except (ValueError, IndexError):
                            pass

                # Pattern 5: "500 for lunch" / "1500 on shoes"
                if not skip_expense and not spend_match and not expense_detected:
                    spend_match = re.search(
                        r'^(?:KES\s*|Ksh\s*)?(\d[\d,]*\.?\d*)\s+(?:for|on)\s+(.+)',
                        clean_msg, re.IGNORECASE
                    )

                # Pattern 6: "gave/sent 500 to/for X" (transfers)
                if not skip_expense and not spend_match and not expense_detected:
                    spend_match = re.search(
                        r'(?:gave|sent|loaned|lent|helped)\s+(?:KES\s*|Ksh\s*)?(\d[\d,]*\.?\d*)\s+(?:to|for)\s+(.+)',
                        clean_msg, re.IGNORECASE
                    )

                # Pattern 7: "gave/sent X 500" (amount after name)
                if not skip_expense and not spend_match and not expense_detected:
                    spend_match = re.search(
                        r'(?:gave|sent|loaned|lent|helped)\s+(\w+)\s+(?:KES\s*|Ksh\s*)?(\d[\d,]*\.?\d*)',
                        clean_msg, re.IGNORECASE
                    )
                    if spend_match:
                        try:
                            amount = float(spend_match.group(2).replace(",", ""))
                            name = spend_match.group(1).strip()
                            desc = f"sent to {name}"
                            expense_detected = True
                        except (ValueError, IndexError):
                            pass

                # Extract amount/desc from standard patterns (groups 1=amount, 2=desc)
                if spend_match and not expense_detected:
                    try:
                        amount = float(spend_match.group(1).replace(",", ""))
                        desc = spend_match.group(2).strip().rstrip('.!?')
                        expense_detected = True
                    except (ValueError, IndexError):
                        pass

                # Log the expense if detected
                if expense_detected and amount and amount > 0 and desc and len(desc) > 1:
                    try:
                        category = _detect_expense_category(desc)
                        if log_expense(user_id, amount, desc, category):
                            daily = get_daily_spending(user_id)
                            today_total = daily["total"] if daily else amount
                            budget_note = ""
                            effective = get_effective_budget(user_id)
                            monthly = get_monthly_spending(user_id)
                            if effective and monthly:
                                remaining = effective - monthly["total"]
                                if remaining > 0:
                                    budget_note = f"\n💰 Monthly: KES {monthly['total']:,.2f} / KES {effective:,.2f} (KES {remaining:,.2f} left)"
                                else:
                                    budget_note = f"\n⚠️ Monthly: KES {monthly['total']:,.2f} / KES {effective:,.2f} — **Over budget!**"
                            log_reply = f"✅ Logged: **KES {amount:,.2f}** — {desc} ({category})\n📊 Today's total: **KES {today_total:,.2f}**{budget_note}"
                            await message.reply(log_reply)
                            add_message_to_history(user_id, "user", [{"text": clean_msg}])
                            add_message_to_history(user_id, "model", [{"text": log_reply}])
                            # Don't return — still let Emily respond naturally about the spending
                    except (ValueError, IndexError):
                        pass  # Not a valid spend, continue normally

            # ─── NATURAL LANGUAGE INCOME DETECTION ───
            if clean_msg:
                income_match = re.search(
                    r'(?:i\s+)?(?:received|got paid|earned|got)\s+(?:KES\s*|Ksh\s*)?(\d[\d,]*\.?\d*)\s+(?:from\s+|for\s+)?(.+)',
                    clean_msg, re.IGNORECASE
                )
                if not income_match:
                    income_match = re.search(
                        r'(?:client|someone)\s+(?:paid|sent)\s+(?:me\s+)?(?:KES\s*|Ksh\s*)?(\d[\d,]*\.?\d*)\s*(?:for\s+)?(.+)?',
                        clean_msg, re.IGNORECASE
                    )
                if income_match:
                    try:
                        amount = float(income_match.group(1).replace(",", ""))
                        desc = (income_match.group(2) or "").strip().rstrip('.!?')
                        if amount > 0 and log_income(user_id, amount, "freelance", desc or "Income"):
                            monthly_inc = get_monthly_income(user_id)
                            month_total = monthly_inc["total"] if monthly_inc else amount
                            log_reply = f"💰 Income logged: **KES {amount:,.2f}**"
                            if desc:
                                log_reply += f" — {desc}"
                            log_reply += f"\n📊 Month income: **KES {month_total:,.2f}**"
                            await message.reply(log_reply)
                            add_message_to_history(user_id, "user", [{"text": clean_msg}])
                            add_message_to_history(user_id, "model", [{"text": log_reply}])
                    except (ValueError, IndexError):
                        pass

            # ─── VOICE CHAT CHANNEL CHECK ───
            is_voice_channel = False
            if message.guild:
                is_voice_channel = is_voice_chat_channel(str(message.guild.id), str(message.channel.id))

            # ─── STOCK AUTO-DETECT ───
            if clean_msg:
                detected_ticker = _detect_stock_query(clean_msg)
                if detected_ticker and not attachment_parts:
                    stock_data = await asyncio.to_thread(get_stock_price, detected_ticker)
                    if stock_data and "couldn't find" not in stock_data:
                        full_response = "Sawa, let me pull that up!\n\n" + stock_data
                        if is_voice_input or wants_voice_reply or user_id in _voice_mode_users or is_voice_channel:
                            if not await send_voice_reply(message, full_response):
                                await send_chunked_reply(message, full_response)
                        else:
                            await send_chunked_reply(message, full_response)
                        add_message_to_history(user_id, "user", [{"text": clean_msg}])
                        add_message_to_history(user_id, "model", [{"text": full_response}])
                        return

            # ─── NATURAL LANGUAGE REMINDER DETECTION ───
            if clean_msg and not clean_msg.startswith("!"):
                reminder_match = re.search(
                    r'remind\s+me\s+(?:to\s+)?(.+?)(?:\s+(?:at|on|in|by|tomorrow|tonight|next)\s+(.+)|$)',
                    clean_msg, re.IGNORECASE
                )
                if reminder_match:
                    try:
                        task = reminder_match.group(1).strip().rstrip('.!?')
                        time_part = reminder_match.group(2) or ""

                        # If no time part was captured, try to parse the whole thing
                        if not time_part:
                            # Check if there's a time embedded at the end
                            time_check = re.search(
                                r'(.+?)\s+(tomorrow|tonight|next\s+\w+|\d{1,2}(?::\d{2})?\s*(?:am|pm)|in\s+\d+\s+(?:hour|minute|min|hr)s?)$',
                                task, re.IGNORECASE
                            )
                            if time_check:
                                task = time_check.group(1).strip()
                                time_part = time_check.group(2).strip()

                        eat_zone = pytz.timezone('Africa/Nairobi')
                        parsed_time = None

                        if time_part:
                            parsed_time = dateparser.parse(
                                time_part,
                                settings={
                                    'PREFER_DATES_FROM': 'future',
                                    'TIMEZONE': 'Africa/Nairobi',
                                    'RETURN_AS_TIMEZONE_AWARE': True,
                                }
                            )

                        if parsed_time and task:
                            if add_reminder(user_id, str(message.channel.id), parsed_time, task):
                                time_str = parsed_time.strftime("%I:%M %p on %b %d")
                                reply = f"⏰ Got it! I'll remind you: **{task}** at **{time_str}** (EAT)"
                                await message.reply(reply)
                                add_message_to_history(user_id, "user", [{"text": clean_msg}])
                                add_message_to_history(user_id, "model", [{"text": reply}])
                                return
                        elif task and not time_part:
                            # No time specified — add as a to-do instead
                            position = add_todo(user_id, task)
                            if position:
                                reply = f"📝 No time specified, so I added it to your to-do list: **{task}** (#{position})\n_Want a reminder instead? Say: remind me to {task} at 5pm_"
                                await message.reply(reply)
                                add_message_to_history(user_id, "user", [{"text": clean_msg}])
                                add_message_to_history(user_id, "model", [{"text": reply}])
                                return
                    except Exception as e:
                        logger.warning(f"NLP reminder detection error: {e}")

            # ─── NATURAL LANGUAGE TO-DO DETECTION ───
            if clean_msg and not clean_msg.startswith("!"):
                todo_match = re.search(
                    r'(?:i\s+need\s+to|i\s+have\s+to|i\s+should|i\s+must|add\s+to\s+my\s+(?:to-?do|list))\s+(.+)',
                    clean_msg, re.IGNORECASE
                )
                if todo_match:
                    try:
                        task = todo_match.group(1).strip().rstrip('.!?')
                        if task and len(task) > 2 and len(task.split()) <= 15:
                            position = add_todo(user_id, task)
                            if position:
                                reply = f"📝 Added to your to-do list: **{task}** (#{position})\n_Mark done: `!done {position}`_"
                                await message.reply(reply)
                                add_message_to_history(user_id, "user", [{"text": clean_msg}])
                                add_message_to_history(user_id, "model", [{"text": reply}])
                                # Don't return — let Emily comment on it too
                    except Exception as e:
                        logger.warning(f"NLP todo detection error: {e}")

            # ─── MEDIA REQUEST PRE-DETECT ───
            # If user asks for an image/gif, search immediately so we can append
            # the result to whatever the AI says (don't rely on AI using tags)
            pre_media_url = None
            pre_media_type = None
            if clean_msg:
                msg_lower = clean_msg.lower()
                # GIF request
                gif_match = re.search(
                    r'(?:show|send|share|get|find)\s+(?:me\s+)?(?:a\s+)?(?:gif|animated)\s+(?:of\s+)?(.+)',
                    msg_lower
                )
                if not gif_match:
                    gif_match = re.search(r'gif\s+(?:of\s+)?(.+)', msg_lower)

                # Image request
                img_match = re.search(
                    r'(?:show|send|share|get|find)\s+(?:me\s+)?(?:a\s+)?(?:picture|image|photo|pic)\s+(?:of\s+)?(.+)',
                    msg_lower
                )
                if not img_match:
                    img_match = re.search(
                        r'(?:picture|image|photo|pic)\s+(?:of\s+)?(.+)',
                        msg_lower
                    )

                if gif_match:
                    search_term = gif_match.group(1).strip().rstrip('?!.')
                    if search_term:
                        pre_media_url = await asyncio.to_thread(get_media_link, search_term, True)
                        pre_media_type = "gif"
                        logger.info(f"Pre-detected GIF request: '{search_term}' -> {pre_media_url}")
                elif img_match:
                    search_term = img_match.group(1).strip().rstrip('?!.')
                    if search_term:
                        pre_media_url = await asyncio.to_thread(get_media_link, search_term, False)
                        pre_media_type = "image"
                        logger.info(f"Pre-detected image request: '{search_term}' -> {pre_media_url}")

            # ─── HIVE MIND ROUTING ───
            chosen_model, route_reason = _route_to_model(
                clean_msg, 
                has_attachments=bool(attachment_parts),
                attachment_types=attachment_types,
            )

            # ─── AI RESPONSE ───
            history = get_chat_history(user_id)
            history.append({"role": "user", "parts": user_parts})

            response_text, source_links = await get_ai_response(
                history, user_id, chosen_model, route_reason,
                guild_id=str(message.guild.id) if message.guild else None
            )
            full_response = response_text + source_links

            if is_voice_input or wants_voice_reply or user_id in _voice_mode_users or is_voice_channel:
                # Send voice note + text fallback
                voice_sent = await send_voice_reply(message, response_text)
                if not voice_sent:
                    await send_chunked_reply(message, full_response)
                elif len(response_text) > 200 or source_links:
                    # Also send text version for long responses or when sources exist
                    await send_chunked_reply(message, full_response)
            else:
                await send_chunked_reply(message, full_response)

            # Send pre-detected media as a SEPARATE message so Discord embeds it
            if pre_media_url:
                try:
                    await message.channel.send(pre_media_url)
                    logger.info(f"Media sent: {pre_media_type} -> {pre_media_url[:80]}")
                except Exception as e:
                    logger.error(f"Failed to send media URL: {e}")

            text_for_history = clean_msg or "Sent a file"
            add_message_to_history(user_id, "user", [{"text": text_for_history}])
            add_message_to_history(user_id, "model", [{"text": response_text}])


# ══════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════
if __name__ == "__main__":
    health_ready = threading.Event()
    threading.Thread(target=run_health_server, args=(health_ready,), daemon=True).start()
    health_ready.wait(timeout=10)

    token = os.getenv("DISCORD_TOKEN")
    if token:
        bot.run(token)
    else:
        logger.error("No DISCORD_TOKEN found!")

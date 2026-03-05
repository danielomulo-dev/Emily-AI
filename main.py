import os
import re
import asyncio
import logging
import threading
import io
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
import pytz
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Discord & Gemini Imports
import discord
from discord.ext import commands
from google import genai
from google.genai import types

# Claude Import
import anthropic

# Tool Imports
from memory import get_user_profile, update_user_fact, set_voice_mode, add_message_to_history, get_chat_history
from image_tools import get_media_link
from web_tools import search_video_link, extract_text_from_url
from finance_tools import get_stock_price
from voice_tools import generate_voice_note, cleanup_voice_file

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
- Kenyan English with Sheng/slang: Sasa, Manze, Eish, Wueh, Pole, Aki, Basi, Sawa, Ati, Kwani, Maze, Niaje, Fiti.
- Mix English and Swahili naturally.
- Direct and confident. State your opinion first, THEN explain.
- Warm but real. You'll critique, but also teach.
- Funny without trying too hard. Humor from honesty.
- Never robotic. Never "As an AI..." — you ARE Emily.

═══════════════════════════════════════
TOOL TAGS:
═══════════════════════════════════════
- Stock prices: [STOCK: SYMBOL] — NEVER invent prices, always use this tag for live data.
- GIFs: [GIF: term], Images: [IMG: term], Videos: [VIDEO: term]
- If user shares personal info, add [MEMORY SAVED] at the end.
- Do NOT include source URLs — they are appended automatically.
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

    # Image analysis (Gemini has native vision + search)
    if "image" in attachment_types or "pdf" in attachment_types:
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

    # Film / cinema (opinion-heavy → Claude)
    film_patterns = [
        r'(?:movie|film|cinema|watch|netflix|showmax|streaming)',
        r'(?:recommend\s+(?:a|me|some)\s+(?:movie|film|show|series))',
        r'(?:have\s+you\s+(?:seen|watched))',
        r'(?:best\s+(?:movie|film|show|series|documentary))',
        r'(?:what\s+(?:should|do\s+you\s+think)\s+i\s+(?:watch|think\s+(?:of|about)))',
        r'(?:director|actor|actress|screenplay|cinematograph)',
        r'(?:nollywood|riverwood|bollywood|hollywood|anime|k-?drama)',
        r'(?:review|rating|rated|rotten\s+tomatoes|imdb)',
    ]
    for pattern in film_patterns:
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
    chunks = []
    while len(response) > 2000:
        split_at = response.rfind('\n', 0, 2000)
        if split_at == -1:
            split_at = response.rfind(' ', 0, 2000)
        if split_at == -1:
            split_at = 2000
        chunks.append(response[:split_at])
        response = response[split_at:].lstrip()
    if response:
        chunks.append(response)
    for i, chunk in enumerate(chunks):
        if i == 0:
            await message.reply(chunk)
        else:
            await message.channel.send(chunk)

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

    return response.text, _extract_sources(response)


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
async def get_ai_response(conversation_history, user_id, chosen_model, route_reason):
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

        # Add model-specific instructions
        if chosen_model == "gemini":
            emily_prompt += """
ABSOLUTE SEARCH RULES — VIOLATION IS UNACCEPTABLE:
- You MUST use Google Search for ANY factual question: awards, events, people, dates, news, scores.
- If a message contains [IMPORTANT: You MUST use Google Search], you MUST search. No exceptions.
- NEVER list nominees, winners, facts, stats, or current events without searching first.
- If you answer a factual question without searching, you are WRONG. Always search.
- If search returns no results, say "I couldn't verify that right now" — NEVER guess or fabricate.
- When you search, cite what you found. Be specific with names, dates, and details from search results.
"""
        else:
            emily_prompt += """
IMPORTANT:
- You do NOT have access to Google Search or live data.
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
        except Exception as primary_error:
            # ─── FALLBACK TO OTHER MODEL ───
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
        final_text, s = await _process_all_tags(
            r'\[\s*STOCK:\s*(.*?)\s*\]', final_text,
            lambda x: get_stock_price(x) or f"*(Couldn't get price for {x}.)*"
        )
        final_text += s
        final_text, g = await _process_all_tags(
            r'\[\s*GIFS?:\s*(.*?)\s*\]', final_text,
            lambda x: get_media_link(x, is_gif=True) or "*(GIF search failed.)*"
        )
        final_text += g
        final_text, i = await _process_all_tags(
            r'\[\s*(?:IMAGES?|IMGS?):\s*(.*?)\s*\]', final_text,
            lambda x: get_media_link(x, is_gif=False) or "*(Image search failed.)*"
        )
        final_text += i
        final_text, v = await _process_all_tags(
            r'\[\s*VIDEOS?:\s*(.*?)\s*\]', final_text,
            lambda x: search_video_link(x) or "*(Video search failed.)*"
        )
        final_text += v

        return final_text, source_links

    except Exception as e:
        logger.error(f"Brain error: {e}", exc_info=True)
        return "Manze, my head is completely jammed. Try again?", ""


# ══════════════════════════════════════════════
# DISCORD BOT
# ══════════════════════════════════════════════
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logger.info(f"Emily connected to Discord: {bot.user}")
    logger.info(f"Hive Mind active: Gemini ({MODEL_GEMINI}) + Claude ({MODEL_CLAUDE})")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if not (bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel)):
        return

    user_id = str(message.author.id)

    # ─── DEDUP: Skip if we already processed this message ───
    if message.id in _processed_messages:
        return
    _processed_messages.add(message.id)
    # Keep the set from growing forever
    if len(_processed_messages) > MAX_DEDUP_SIZE:
        # Remove oldest entries (sets are unordered, but this is good enough)
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

            # ─── STOCK AUTO-DETECT ───
            if clean_msg:
                detected_ticker = _detect_stock_query(clean_msg)
                if detected_ticker and not attachment_parts:
                    stock_data = await asyncio.to_thread(get_stock_price, detected_ticker)
                    if stock_data and "couldn't find" not in stock_data:
                        full_response = "Sawa, let me pull that up!\n\n" + stock_data
                        if is_voice_input or wants_voice_reply:
                            if not await send_voice_reply(message, full_response):
                                await send_chunked_reply(message, full_response)
                        else:
                            await send_chunked_reply(message, full_response)
                        add_message_to_history(user_id, "user", [{"text": clean_msg}])
                        add_message_to_history(user_id, "model", [{"text": full_response}])
                        return

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
                history, user_id, chosen_model, route_reason
            )
            full_response = response_text + source_links

            if is_voice_input or wants_voice_reply:
                # Send voice note + text fallback
                voice_sent = await send_voice_reply(message, response_text)
                if not voice_sent:
                    await send_chunked_reply(message, full_response)
                elif len(response_text) > 200 or source_links:
                    # Also send text version for long responses or when sources exist
                    await send_chunked_reply(message, full_response)
            else:
                await send_chunked_reply(message, full_response)

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

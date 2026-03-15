import os
import logging
import requests
import io
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# --- CONSTANTS ---
DEFAULT_MAX_CHARS = 3000
RESEARCH_MAX_CHARS = 15000

# --- GOOGLE CUSTOM SEARCH CONFIG ---
GOOGLE_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_SEARCH_CX")
GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"


def _google_configured():
    return bool(GOOGLE_API_KEY and GOOGLE_CX)


# ══════════════════════════════════════════════
# GOOGLE CUSTOM SEARCH
# ══════════════════════════════════════════════
def _google_search(query, search_type="web", max_results=5):
    """Search using Google Custom Search API.
    search_type: 'web' for general, 'news' for news-focused queries
    """
    if not _google_configured():
        return None

    try:
        params = {
            "key": GOOGLE_API_KEY,
            "cx": GOOGLE_CX,
            "q": query,
            "num": min(max_results, 10),
        }

        # For news, sort by date and restrict to last 3 days
        if search_type == "news":
            params["sort"] = "date"
            params["dateRestrict"] = "d3"

        response = requests.get(GOOGLE_SEARCH_URL, params=params, timeout=10)

        if response.status_code == 200:
            data = response.json()
            items = data.get("items", [])
            logger.info(f"Google search '{query}': {len(items)} results")
            return items
        elif response.status_code == 429:
            logger.warning("Google Search API rate limited (daily quota reached)")
            return None
        else:
            logger.error(f"Google Search error: {response.status_code} — {response.text[:200]}")
            return None

    except Exception as e:
        logger.error(f"Google search error: {e}")
        return None


def _google_news_search(topic, max_results=5):
    """Search Google for recent news on a topic."""
    query = f"{topic} news"
    items = _google_search(query, search_type="news", max_results=max_results)
    if not items:
        return None

    results = []
    for item in items:
        results.append({
            "title": item.get("title", "No Title"),
            "url": item.get("link", "#"),
            "source": item.get("displayLink", "Unknown"),
            "snippet": item.get("snippet", ""),
        })

    return results


def _google_web_search(query, max_results=3):
    """Search Google for web results and return URLs."""
    items = _google_search(query, search_type="web", max_results=max_results)
    if not items:
        return None

    return [item.get("link", "") for item in items if item.get("link")]


# ══════════════════════════════════════════════
# DUCKDUCKGO FALLBACK
# ══════════════════════════════════════════════
def _ddg_news_search(topic, max_results=5):
    """Fallback news search using DuckDuckGo."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.news(
                keywords=topic, region="wt-wt",
                safesearch="moderate", max_results=max_results
            ))
            if not results:
                return None

            formatted = []
            for r in results:
                formatted.append({
                    "title": r.get("title", "No Title"),
                    "url": r.get("url", "#"),
                    "source": r.get("source", "Unknown"),
                    "date": r.get("date", ""),
                })
            return formatted
    except Exception as e:
        logger.error(f"DDG news fallback error: {e}")
        return None


def _ddg_web_search(query, max_results=3):
    """Fallback web search using DuckDuckGo."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(
                query, region="wt-wt",
                safesearch="moderate", max_results=max_results
            ))
            return [r["href"] for r in results]
    except Exception as e:
        logger.error(f"DDG web fallback error: {e}")
        return []


def _ddg_video_search(query):
    """Fallback video search using DuckDuckGo."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.videos(
                keywords=f"site:youtube.com {query}", max_results=1
            ))
            if results:
                return results[0].get("content")
            return None
    except Exception:
        return None


# ══════════════════════════════════════════════
# DEDUP HELPER
# ══════════════════════════════════════════════
def _dedup_results(results, exclude_urls=None):
    """Remove duplicate articles by URL and title similarity."""
    exclude = exclude_urls or set()
    fresh = []

    for r in results:
        url = r.get("url", "")
        title = r.get("title", "").lower()

        # Skip already-sent URLs
        if url in exclude:
            continue

        # Skip similar titles
        is_dupe = False
        for fr in fresh:
            existing_title = fr.get("title", "").lower()
            existing_words = set(existing_title.split())
            new_words = set(title.split())
            if existing_words and new_words:
                overlap = len(existing_words & new_words) / max(len(existing_words), len(new_words))
                if overlap > 0.6:
                    is_dupe = True
                    break

        if not is_dupe:
            fresh.append(r)

    return fresh


# ══════════════════════════════════════════════
# PUBLIC API: NEWS SEARCH
# ══════════════════════════════════════════════
def get_latest_news(topic, max_results=5, exclude_urls=None):
    """Fetch latest news — Google first, DuckDuckGo fallback."""
    try:
        logger.info(f"Fetching news for: {topic}")

        # Try Google first
        results = None
        source = "google"
        if _google_configured():
            results = _google_news_search(topic, max_results=max_results + 5)

        # Fallback to DuckDuckGo
        if not results:
            source = "ddg"
            results = _ddg_news_search(topic, max_results=max_results + 5)

        if not results:
            return None, []

        # Dedup
        fresh = _dedup_results(results, exclude_urls)
        if not fresh:
            return None, []

        fresh = fresh[:max_results]

        # Format output
        sent_urls = []
        news_summary = f"📰 **Latest News: {topic}**\n"
        for r in fresh:
            title = r.get("title", "No Title")
            url = r.get("url", "#")
            src = r.get("source", "Unknown")
            date = r.get("date", "")
            date_str = f" ({date})" if date else ""
            news_summary += f"• [{title}]({url}) - *{src}*{date_str}\n"
            if url and url != "#":
                sent_urls.append(url)

        logger.info(f"News for '{topic}': {len(fresh)} articles via {source}")
        return news_summary, sent_urls

    except Exception as e:
        logger.error(f"News search error: {e}")
        return None, []


# ══════════════════════════════════════════════
# PUBLIC API: WEB SEARCH
# ══════════════════════════════════════════════
def get_search_results(query, max_results=3):
    """Search the web — Google first, DuckDuckGo fallback."""
    try:
        logger.info(f"Searching: {query}")

        # Try Google first
        if _google_configured():
            urls = _google_web_search(query, max_results=max_results)
            if urls:
                return urls

        # Fallback to DuckDuckGo
        return _ddg_web_search(query, max_results=max_results)

    except Exception as e:
        logger.error(f"Search error: {e}")
        return []


# ══════════════════════════════════════════════
# PUBLIC API: VIDEO SEARCH
# ══════════════════════════════════════════════
def search_video_link(query):
    """Search for a YouTube video — Google first, DuckDuckGo fallback."""
    try:
        # Try Google
        if _google_configured():
            items = _google_search(f"site:youtube.com {query}", max_results=1)
            if items:
                url = items[0].get("link", "")
                if "youtube.com" in url or "youtu.be" in url:
                    return url

        # Fallback to DuckDuckGo
        return _ddg_video_search(query)

    except Exception:
        return None


# ══════════════════════════════════════════════
# CONTENT EXTRACTION
# ══════════════════════════════════════════════
def extract_text_from_url(url, max_chars=None):
    _max = max_chars or DEFAULT_MAX_CHARS

    # 1. YouTube
    if "youtube.com" in url or "youtu.be" in url:
        return get_youtube_transcript(url, max_chars=_max)

    # 2. General Websites & PDFs
    return get_website_content(url, max_chars=_max)


def get_website_content(url, max_chars=None):
    _max = max_chars or DEFAULT_MAX_CHARS
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.google.com/'
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # CHECK IF IT IS A PDF
        content_type = response.headers.get('Content-Type', '').lower()
        if 'application/pdf' in content_type or url.endswith('.pdf'):
            return extract_online_pdf(response.content, url, _max)

        # OTHERWISE, PARSE HTML
        soup = BeautifulSoup(response.content, 'html.parser')

        # Remove junk
        for script in soup(["script", "style", "nav", "footer", "header", "aside", "form", "iframe", "ads"]):
            script.extract()

        text = soup.get_text()
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)

        return f"\n\n--- SOURCE: {url} ---\n{text[:_max]}..."

    except Exception as e:
        logger.warning(f"Failed to read {url}: {e}")
        return f"\n[Could not read {url}: {e}]"


def extract_online_pdf(file_bytes, url, max_chars):
    """Helper to read PDFs found via search."""
    try:
        from pypdf import PdfReader
        pdf_file = io.BytesIO(file_bytes)
        reader = PdfReader(pdf_file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
            if len(text) > max_chars:
                break
        return f"\n\n--- PDF SOURCE: {url} ---\n{text[:max_chars]}..."
    except Exception as e:
        return f"\n[Error reading PDF {url}: {e}]"


def get_youtube_transcript(url, max_chars=None):
    _max = max_chars or DEFAULT_MAX_CHARS
    try:
        video_id = None
        if "youtu.be" in url:
            video_id = url.split("/")[-1]
        elif "v=" in url:
            video_id = parse_qs(urlparse(url).query)['v'][0]
        if not video_id:
            return ""
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
        full_text = " ".join([t['text'] for t in transcript_list])
        return f"\n\n--- VIDEO TRANSCRIPT: {url} ---\n{full_text[:_max]}..."
    except Exception:
        return ""


def get_news_raw(topic, max_results=5, exclude_urls=None):
    """Fetch latest news as structured data (for AI commentary).
    Returns list of dicts with: title, url, source, snippet, date
    """
    try:
        results = None
        if _google_configured():
            results = _google_news_search(topic, max_results=max_results + 5)
        if not results:
            results = _ddg_news_search(topic, max_results=max_results + 5)
        if not results:
            return [], []

        fresh = _dedup_results(results, exclude_urls)
        if not fresh:
            return [], []

        fresh = fresh[:max_results]
        sent_urls = [r.get("url", "") for r in fresh if r.get("url")]
        return fresh, sent_urls

    except Exception as e:
        logger.error(f"Raw news fetch error: {e}")
        return [], []

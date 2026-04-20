import os
import logging
import time as _time
import ipaddress
import socket
import requests
import io
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# --- CACHING ---
_news_cache = {}  # {topic: {"result": ..., "time": timestamp}}
_NEWS_CACHE_TTL = 900  # 15 minutes

# --- CONSTANTS ---
DEFAULT_MAX_CHARS = 3000
RESEARCH_MAX_CHARS = 15000

# --- GOOGLE CUSTOM SEARCH CONFIG ---
GOOGLE_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_SEARCH_CX")
GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"


# ══════════════════════════════════════════════
# SSRF PROTECTION
# ══════════════════════════════════════════════
def _is_safe_url(url):
    """Return True if the URL is safe to fetch (not pointing to private/loopback/metadata).

    Guards against SSRF via user-supplied links. Resolves the hostname and
    refuses private, loopback, link-local, multicast or reserved IPs — including
    cloud metadata (169.254.169.254) and localhost.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False

        # Resolve the hostname — this catches DNS rebinding-style tricks where
        # attacker.com resolves to 127.0.0.1
        try:
            infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return False

        for info in infos:
            sockaddr = info[4]
            try:
                ip = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                return False
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
                logger.warning(f"SSRF block: {url} resolves to {ip}")
                return False
        return True
    except Exception as e:
        logger.warning(f"SSRF check failed for {url}: {e}")
        return False


# ══════════════════════════════════════════════
# YOUTUBE URL PARSING
# ══════════════════════════════════════════════
def _extract_youtube_video_id(url):
    """Extract a YouTube video ID from the common URL shapes.

    Handles:
      https://youtu.be/<id>
      https://youtu.be/<id>?si=...       (share-link query string)
      https://www.youtube.com/watch?v=<id>
      https://m.youtube.com/watch?v=<id>&t=30s
      https://www.youtube.com/shorts/<id>
      https://www.youtube.com/embed/<id>
    Returns None if no ID can be extracted.
    """
    try:
        parsed = urlparse(url)
        # youtu.be/<id>
        if "youtu.be" in (parsed.netloc or ""):
            first = parsed.path.lstrip("/").split("/")[0]
            return first or None
        # youtube.com/watch?v=<id>
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            return qs["v"][0]
        # /shorts/<id>, /embed/<id>, /v/<id>
        path = parsed.path or ""
        for prefix in ("/shorts/", "/embed/", "/v/"):
            if path.startswith(prefix):
                rest = path[len(prefix):].split("/")[0]
                return rest or None
        return None
    except Exception:
        return None


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
        try:
            from ddgs import DDGS
            results = DDGS().news(query=topic, region="us-en",
                safesearch="moderate", max_results=max_results)
        except ImportError:
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
        try:
            from ddgs import DDGS
            results = DDGS().text(query=query, region="us-en",
                safesearch="moderate", max_results=max_results)
        except ImportError:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(
                    query, region="wt-wt",
                    safesearch="moderate", max_results=max_results
                ))
        return [r.get("href", r.get("url", "")) for r in (results or [])]
    except Exception as e:
        logger.error(f"DDG web fallback error: {e}")
        return []


def _ddg_video_search(query):
    """Fallback video search using DuckDuckGo."""
    try:
        try:
            from ddgs import DDGS
            results = DDGS().videos(query=f"site:youtube.com {query}", max_results=1)
        except ImportError:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.videos(
                    keywords=f"site:youtube.com {query}", max_results=1
                ))
        if results:
            return results[0].get("content") or results[0].get("url")
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
    """Fetch latest news — Google first, DuckDuckGo fallback. Cached 15 min."""
    try:
        # Check cache (only for standard requests without exclusions)
        cache_key = f"{topic.lower()}:{max_results}"
        if not exclude_urls:
            cached = _news_cache.get(cache_key)
            if cached and _time.time() - cached["time"] < _NEWS_CACHE_TTL:
                logger.info(f"News cache hit for '{topic}'")
                return cached["result"], cached.get("urls", [])

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

        # Store in cache
        if not exclude_urls:
            _news_cache[cache_key] = {"result": news_summary, "urls": sent_urls, "time": _time.time()}

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

    # SSRF guard — refuse URLs resolving to private/loopback/metadata IPs.
    if not _is_safe_url(url):
        return f"\n[Refusing to fetch {url}: URL is not publicly reachable.]"

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.google.com/'
        }

        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        response.raise_for_status()

        # Re-check the final URL in case we followed a redirect to an internal host.
        if response.url != url and not _is_safe_url(response.url):
            return f"\n[Refusing to fetch {url}: redirect target is not publicly reachable.]"

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
        video_id = _extract_youtube_video_id(url)
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

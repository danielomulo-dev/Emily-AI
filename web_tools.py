import logging
import requests
import io
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs
from duckduckgo_search import DDGS
from pypdf import PdfReader # <--- New Import for online PDFs

logger = logging.getLogger(__name__)

# --- CONSTANTS ---
DEFAULT_MAX_CHARS = 3000
RESEARCH_MAX_CHARS = 15000

# --- NEWS SEARCH ---
def get_latest_news(topic, max_results=5):
    try:
        logger.info(f"Fetching news for: {topic}")
        with DDGS() as ddgs:
            results = list(ddgs.news(keywords=topic, region="wt-wt", safesearch="moderate", max_results=max_results))
            if not results: return None
            news_summary = f"📰 **Latest News: {topic}**\n"
            for r in results:
                title = r.get('title', 'No Title')
                source = r.get('source', 'Unknown')
                date = r.get('date', '')
                url = r.get('url', '#')
                news_summary += f"• [{title}]({url}) - *{source}* ({date})\n"
            return news_summary
    except Exception as e:
        logger.error(f"News search error: {e}")
        return None

# --- WEB SEARCH ---
def get_search_results(query, max_results=3):
    try:
        logger.info(f"Searching: {query}")
        with DDGS() as ddgs:
            results = list(ddgs.text(query, region="wt-wt", safesearch="moderate", max_results=max_results))
            urls = [r['href'] for r in results]
            return urls
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []

# --- CONTENT EXTRACTION ---
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
        # Use a "Stealth" User-Agent to look like a real PC, not a bot
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.google.com/'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status() # Check for 403/404 errors

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
        pdf_file = io.BytesIO(file_bytes)
        reader = PdfReader(pdf_file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
            if len(text) > max_chars: break
        return f"\n\n--- PDF SOURCE: {url} ---\n{text[:max_chars]}..."
    except Exception as e:
        return f"\n[Error reading PDF {url}: {e}]"

def get_youtube_transcript(url, max_chars=None):
    _max = max_chars or DEFAULT_MAX_CHARS
    try:
        video_id = None
        if "youtu.be" in url: video_id = url.split("/")[-1]
        elif "v=" in url: video_id = parse_qs(urlparse(url).query)['v'][0]
        if not video_id: return ""
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
        full_text = " ".join([t['text'] for t in transcript_list])
        return f"\n\n--- VIDEO TRANSCRIPT: {url} ---\n{full_text[:_max]}..."
    except: return ""

def search_video_link(query):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.videos(keywords=f"site:youtube.com {query}", max_results=1))
            if results: return results[0]['content']
            return None
    except: return None
import os
import logging
import random
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# --- GOOGLE IMAGE SEARCH CONFIG ---
GOOGLE_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_SEARCH_CX")

# File extensions that Discord can embed as previews
VALID_IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp')

# Domains that often block hotlinking or return tiny placeholder images
BLOCKED_DOMAINS = [
    'shutterstock.com', 'gettyimages.com', 'istockphoto.com',
    'alamy.com', 'dreamstime.com', '123rf.com',
    'stock.adobe.com', 'depositphotos.com',
    'pinterest.com', 'facebook.com', 'instagram.com',
]


def _is_valid_image_url(url: str) -> bool:
    """Quick check that a URL is likely a real, embeddable image."""
    if not url or not url.startswith('http'):
        return False
    lower = url.lower()
    for domain in BLOCKED_DOMAINS:
        if domain in lower:
            return False
    return True


def _verify_image_loads(url: str, timeout: int = 5) -> bool:
    """HEAD request to check the image URL actually resolves."""
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True,
                             headers={'User-Agent': 'Mozilla/5.0'})
        content_type = resp.headers.get('Content-Type', '')
        return resp.status_code == 200 and 'image' in content_type
    except Exception:
        return False


# ══════════════════════════════════════════════
# GOOGLE IMAGE SEARCH (primary)
# ══════════════════════════════════════════════
def _google_image_search(query, is_gif=False, max_results=5):
    """Search for images using Google Custom Search API."""
    if not GOOGLE_API_KEY or not GOOGLE_CX:
        return None

    try:
        params = {
            "key": GOOGLE_API_KEY,
            "cx": GOOGLE_CX,
            "q": query,
            "searchType": "image",
            "num": min(max_results, 10),
            "safe": "active",
        }

        if is_gif:
            params["fileType"] = "gif"

        response = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params,
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            items = data.get("items", [])
            urls = []
            for item in items:
                url = item.get("link", "")
                if _is_valid_image_url(url):
                    urls.append(url)
            logger.info(f"Google image search '{query}': {len(urls)} valid results")
            return urls if urls else None
        else:
            logger.warning(f"Google image search error: {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"Google image search error: {e}")
        return None


# ══════════════════════════════════════════════
# DUCKDUCKGO IMAGE SEARCH (fallback)
# ══════════════════════════════════════════════
def _ddg_image_search(query, is_gif=False, max_results=10):
    """Fallback image search using DuckDuckGo."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            file_type = 'gif' if is_gif else None
            results = list(ddgs.images(
                keywords=query,
                region="wt-wt",
                safesearch="moderate",
                max_results=max_results,
                type_image=file_type,
            ))
            urls = [r.get('image', '') for r in results if _is_valid_image_url(r.get('image', ''))]
            return urls if urls else None
    except Exception as e:
        logger.error(f"DDG image search error: {e}")
        return None


# ══════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════
def get_media_link(query, is_gif=False):
    """Search for an image or GIF — Google first, DuckDuckGo fallback.

    Returns a direct image URL that Discord can embed, or None.
    """
    try:
        clean_query = query.strip(' ".,!*')
        logger.info(f"Image search: '{clean_query}' (GIF: {is_gif})")

        # Try Google first
        urls = _google_image_search(clean_query, is_gif=is_gif, max_results=5)

        # Fallback to DuckDuckGo
        if not urls:
            urls = _ddg_image_search(clean_query, is_gif=is_gif, max_results=10)

        if not urls:
            logger.warning(f"No image results for: '{clean_query}'")
            return None

        # Shuffle for variety
        random.shuffle(urls)

        # Try to find one that loads
        for url in urls[:5]:
            if _verify_image_loads(url):
                logger.info(f"Image found (verified): {url[:80]}")
                return url

        # If HEAD checks all fail, return the first one anyway
        # (Discord might still embed it)
        logger.info(f"Image found (unverified): {urls[0][:80]}")
        return urls[0]

    except Exception as e:
        logger.error(f"Image search error: {e}")
        return None
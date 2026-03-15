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

# Domains that often block hotlinking
BLOCKED_DOMAINS = [
    'shutterstock.com', 'gettyimages.com', 'istockphoto.com',
    'alamy.com', 'dreamstime.com', '123rf.com',
    'stock.adobe.com', 'depositphotos.com',
    'pinterest.com', 'facebook.com', 'instagram.com',
]


def _is_valid_image_url(url: str) -> bool:
    if not url or not url.startswith('http'):
        return False
    lower = url.lower()
    return not any(domain in lower for domain in BLOCKED_DOMAINS)


def _verify_image_loads(url: str, timeout: int = 3) -> bool:
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
    """Search via Google Custom Search API with detailed error logging."""
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
            if "gif" not in query.lower():
                params["q"] = f"{query} animated gif"

        response = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params,
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            items = data.get("items", [])
            urls = [item.get("link", "") for item in items if _is_valid_image_url(item.get("link", ""))]
            logger.info(f"Google image search '{query}': {len(urls)} results")
            return urls if urls else None
        elif response.status_code == 429:
            logger.warning("Google image search: QUOTA EXHAUSTED (429)")
            return None
        else:
            try:
                err = response.json().get("error", {})
                err_msg = err.get("message", "Unknown")
                logger.warning(f"Google image search error {response.status_code}: {err_msg}")
            except Exception:
                logger.warning(f"Google image search error: {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"Google image search exception: {e}")
        return None


# ══════════════════════════════════════════════
# DUCKDUCKGO IMAGE SEARCH (fallback 1)
# ══════════════════════════════════════════════
def _ddg_image_search(query, is_gif=False, max_results=10):
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
            logger.info(f"DDG image search '{query}': {len(urls)} results")
            return urls if urls else None
    except Exception as e:
        logger.error(f"DDG image search error: {e}")
        return None


# ══════════════════════════════════════════════
# BING IMAGE SEARCH (fallback 2 — no API key)
# ══════════════════════════════════════════════
def _bing_image_search(query, is_gif=False, max_results=5):
    """Scrape Bing image search — no API key required."""
    try:
        import re as _re
        search_query = f"{query} animated gif" if is_gif else query
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
        response = requests.get(
            "https://www.bing.com/images/search",
            params={"q": search_query, "form": "HDRSC2", "first": "1"},
            headers=headers,
            timeout=10,
        )
        if response.status_code == 200:
            urls = _re.findall(r'murl&quot;:&quot;(https?://[^&]+?)&quot;', response.text)
            valid = [u for u in urls if _is_valid_image_url(u)][:max_results]
            logger.info(f"Bing image search '{query}': {len(valid)} results")
            return valid if valid else None
        else:
            logger.warning(f"Bing image search error: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Bing image search error: {e}")
        return None


# ══════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════
def get_media_link(query, is_gif=False):
    """Search for an image or GIF — tries multiple sources.

    Order: Google → DuckDuckGo → Bing
    Returns a direct image URL that Discord can embed, or None.
    """
    try:
        clean_query = query.strip(' ".,!*')
        logger.info(f"Image search: '{clean_query}' (GIF: {is_gif})")

        # 1. Google (best quality, but has daily quota)
        urls = _google_image_search(clean_query, is_gif=is_gif, max_results=5)
        if urls:
            logger.info(f"Image found (Google): {urls[0][:80]}")
            return urls[0]

        # 2. DuckDuckGo (no quota, but rate-limited on some IPs)
        urls = _ddg_image_search(clean_query, is_gif=is_gif, max_results=5)
        if urls:
            logger.info(f"Image found (DDG): {urls[0][:80]}")
            return urls[0]

        # 3. Bing scrape (no API key needed, no quota)
        urls = _bing_image_search(clean_query, is_gif=is_gif, max_results=5)
        if urls:
            logger.info(f"Image found (Bing): {urls[0][:80]}")
            return urls[0]

        logger.warning(f"All image sources failed for: '{clean_query}'")
        return None

    except Exception as e:
        logger.error(f"Image search error: {e}")
        return None

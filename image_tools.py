import logging
import random
import requests
from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

# File extensions that Discord can embed as previews
VALID_IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp')

# Domains that often block hotlinking or return tiny placeholder images
BLOCKED_DOMAINS = [
    'shutterstock.com', 'gettyimages.com', 'istockphoto.com',
    'alamy.com', 'dreamstime.com', '123rf.com',
    'stock.adobe.com', 'depositphotos.com',
]


def _is_valid_image_url(url: str) -> bool:
    """Quick check that a URL is likely a real, embeddable image."""
    if not url or not url.startswith('http'):
        return False
    # Skip stock photo sites (they block hotlinking)
    lower = url.lower()
    for domain in BLOCKED_DOMAINS:
        if domain in lower:
            return False
    return True


def _verify_image_loads(url: str, timeout: int = 5) -> bool:
    """HEAD request to check the image URL actually resolves.
    Only used as a fallback when first-choice image fails."""
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True,
                             headers={'User-Agent': 'Mozilla/5.0'})
        content_type = resp.headers.get('Content-Type', '')
        return resp.status_code == 200 and 'image' in content_type
    except Exception:
        return False


def get_media_link(query, is_gif=False):
    """Searches for an image or GIF URL using DuckDuckGo.
    
    Returns a direct image URL that Discord can embed, or None.
    Filters out stock photo sites, validates URLs, and retries on failure.
    """
    try:
        clean_query = query.strip(' ".,!*')
        logger.info(f"Image search: '{clean_query}' (GIF: {is_gif})")

        with DDGS() as ddgs:
            file_type = 'gif' if is_gif else None

            results = list(ddgs.images(
                keywords=clean_query,
                region="wt-wt",
                safesearch="moderate",
                max_results=15,  # fetch more so we have fallbacks
                type_image=file_type
            ))

        if not results:
            logger.warning(f"No image results for: '{clean_query}'")
            return None

        # Filter to valid, embeddable URLs
        valid = [r for r in results if _is_valid_image_url(r.get('image', ''))]

        if not valid:
            logger.warning(f"All results filtered out for: '{clean_query}'")
            return None

        # Shuffle and try to find one that actually loads
        random.shuffle(valid)
        
        # Try the first pick directly (fast path — skip HEAD check)
        first_url = valid[0].get('image', '')
        if first_url:
            logger.info(f"Image found: {first_url[:80]}")
            return first_url

        # If first pick is somehow empty, verify others
        for result in valid[1:5]:  # check up to 4 more
            url = result.get('image', '')
            if url and _verify_image_loads(url):
                logger.info(f"Image found (verified): {url[:80]}")
                return url

        logger.warning(f"No loadable image for: '{clean_query}'")
        return None

    except Exception as e:
        logger.error(f"Image search error: {e}")
        return None
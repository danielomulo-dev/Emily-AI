import os
import logging
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# --- REDDIT CONFIG ---
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "EmilyAI/1.0 by EmilyBot")
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API_URL = "https://oauth.reddit.com"

# --- TOKEN CACHE ---
_token_cache = {"token": None, "expires_at": None}

# --- DEFAULT SUBREDDITS ---
INVESTMENT_SUBS = ["wallstreetbets", "investing", "stocks", "personalfinance", "CryptoCurrency"]
FINANCE_KENYA_SUBS = ["Kenya", "NairobiCity"]


def is_configured():
    return bool(REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET)


# ══════════════════════════════════════════════
# AUTHENTICATION
# ══════════════════════════════════════════════
def _get_token():
    """Get Reddit OAuth token using client credentials."""
    if _token_cache["token"] and _token_cache["expires_at"] and \
       datetime.now() < _token_cache["expires_at"]:
        return _token_cache["token"]

    try:
        response = requests.post(
            REDDIT_TOKEN_URL,
            auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": REDDIT_USER_AGENT},
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            _token_cache["token"] = data["access_token"]
            _token_cache["expires_at"] = datetime.now() + timedelta(seconds=data.get("expires_in", 3600) - 60)
            logger.info("Reddit token refreshed")
            return _token_cache["token"]
        else:
            logger.error(f"Reddit token error: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Reddit auth error: {e}")
        return None


def _reddit_get(endpoint, params=None):
    """Make authenticated GET to Reddit API with retry."""
    token = _get_token()
    if not token:
        return None

    for attempt in range(3):
        try:
            response = requests.get(
                f"{REDDIT_API_URL}{endpoint}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": REDDIT_USER_AGENT,
                },
                params=params,
                timeout=10,
            )
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 2))
                logger.warning(f"Reddit rate limited, waiting {retry_after}s")
                import time
                time.sleep(retry_after)
                continue
            else:
                logger.error(f"Reddit API error: {response.status_code}")
                return None
        except requests.exceptions.Timeout:
            logger.warning(f"Reddit timeout (attempt {attempt + 1})")
            if attempt < 2:
                import time
                time.sleep(1)
                continue
            return None
        except Exception as e:
            logger.error(f"Reddit request error: {e}")
            return None
    return None


# ══════════════════════════════════════════════
# FETCH POSTS
# ══════════════════════════════════════════════
def get_trending_posts(subreddit, sort="hot", limit=5, time_filter="day"):
    """
    Fetch trending posts from a subreddit.
    sort: hot, new, top, rising
    time_filter: hour, day, week, month, year, all (only for 'top')
    """
    if not is_configured():
        return None, "Reddit not configured"

    try:
        params = {"limit": limit}
        if sort == "top":
            params["t"] = time_filter

        data = _reddit_get(f"/r/{subreddit}/{sort}", params)
        if not data:
            return None, f"Couldn't fetch r/{subreddit}"

        posts = []
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            posts.append({
                "title": post.get("title", ""),
                "author": post.get("author", "[deleted]"),
                "score": post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "url": f"https://reddit.com{post.get('permalink', '')}",
                "selftext": (post.get("selftext", "") or "")[:300],
                "is_self": post.get("is_self", False),
                "link_url": post.get("url", ""),
                "subreddit": post.get("subreddit", subreddit),
                "created_utc": post.get("created_utc", 0),
                "flair": post.get("link_flair_text", ""),
            })

        return posts, None
    except Exception as e:
        logger.error(f"Reddit fetch error: {e}")
        return None, f"Error: {e}"


def get_multi_subreddit_posts(subreddits, sort="hot", limit=3):
    """Fetch posts from multiple subreddits."""
    all_posts = []
    for sub in subreddits:
        posts, error = get_trending_posts(sub, sort=sort, limit=limit)
        if posts:
            all_posts.extend(posts)
    # Sort by score
    all_posts.sort(key=lambda x: x["score"], reverse=True)
    return all_posts


def search_reddit(query, subreddit=None, sort="relevance", limit=5):
    """Search Reddit for a topic."""
    if not is_configured():
        return None, "Reddit not configured"

    try:
        endpoint = f"/r/{subreddit}/search" if subreddit else "/search"
        params = {
            "q": query,
            "sort": sort,
            "limit": limit,
            "restrict_sr": "true" if subreddit else "false",
            "t": "week",
        }

        data = _reddit_get(endpoint, params)
        if not data:
            return None, "Search failed"

        posts = []
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            posts.append({
                "title": post.get("title", ""),
                "author": post.get("author", "[deleted]"),
                "score": post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "url": f"https://reddit.com{post.get('permalink', '')}",
                "selftext": (post.get("selftext", "") or "")[:200],
                "subreddit": post.get("subreddit", ""),
            })

        return posts, None
    except Exception as e:
        logger.error(f"Reddit search error: {e}")
        return None, f"Error: {e}"


# ══════════════════════════════════════════════
# INVESTMENT DISCUSSIONS
# ══════════════════════════════════════════════
def get_investment_buzz(limit=5):
    """Get top investment discussions from finance subreddits."""
    posts = get_multi_subreddit_posts(INVESTMENT_SUBS, sort="hot", limit=limit)
    return posts[:limit] if posts else []


def get_stock_mentions(ticker, limit=5):
    """Search Reddit for discussions about a specific stock."""
    posts, error = search_reddit(
        f"${ticker} OR {ticker}",
        subreddit="wallstreetbets+investing+stocks",
        sort="hot",
        limit=limit,
    )
    return posts, error


# ══════════════════════════════════════════════
# FORMATTED OUTPUTS
# ══════════════════════════════════════════════
def format_reddit_posts(posts, title="Reddit"):
    """Format posts for Discord."""
    if not posts:
        return "No posts found!"

    lines = [f"📱 **{title}**\n"]
    for i, p in enumerate(posts, 1):
        score = p["score"]
        comments = p["num_comments"]
        flair = f" [{p['flair']}]" if p.get("flair") else ""
        sub = f"r/{p['subreddit']}"

        # Score formatting
        if score >= 1000:
            score_str = f"{score/1000:.1f}k"
        else:
            score_str = str(score)

        lines.append(f"**{i}.** [{p['title']}]({p['url']})")
        lines.append(f"   ⬆️ {score_str} · 💬 {comments} · {sub}{flair}")

        # Show preview for self posts
        if p.get("selftext") and len(p["selftext"]) > 20:
            preview = p["selftext"][:150].replace("\n", " ")
            lines.append(f"   *{preview}...*")

        lines.append("")

    return "\n".join(lines)


def format_investment_buzz(posts):
    """Format investment discussions."""
    if not posts:
        return "No investment buzz right now. Markets must be sleeping!"

    lines = ["📈 **Investment Buzz — What Reddit Is Talking About**\n"]
    for i, p in enumerate(posts, 1):
        score = p["score"]
        if score >= 1000:
            score_str = f"{score/1000:.1f}k"
        else:
            score_str = str(score)

        sub = f"r/{p['subreddit']}"
        lines.append(f"**{i}.** [{p['title']}]({p['url']})")
        lines.append(f"   ⬆️ {score_str} · 💬 {p['num_comments']} · {sub}")
        lines.append("")

    lines.append("_⚠️ Reddit is not financial advice! Do your own research, manze._")
    return "\n".join(lines)


def format_stock_mentions(posts, ticker):
    """Format stock-specific Reddit discussions."""
    if not posts:
        return f"No recent Reddit discussions about **{ticker}**."

    lines = [f"🔍 **Reddit Discussions: {ticker}**\n"]
    for i, p in enumerate(posts, 1):
        score = p["score"]
        if score >= 1000:
            score_str = f"{score/1000:.1f}k"
        else:
            score_str = str(score)

        lines.append(f"**{i}.** [{p['title']}]({p['url']})")
        lines.append(f"   ⬆️ {score_str} · 💬 {p['num_comments']} · r/{p['subreddit']}")
        lines.append("")

    lines.append(f"_What Reddit thinks about {ticker} — not financial advice!_")
    return "\n".join(lines)

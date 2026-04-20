"""
job_scout_tools.py — Emily's job scout module.

Fetches remote + Kenya-based jobs from free public APIs, scores them against
Daniel's skill profile, categorizes as DESIGN / DEV / HYBRID, and surfaces
70+ scoring matches to him via DM.

Sources used (all free, no auth required as of writing):
- RemoteOK:   https://remoteok.com/api
- Remotive:   https://remotive.com/api/remote-jobs
- Arbeitnow:  https://www.arbeitnow.com/api/job-board-api
- Himalayas:  https://himalayas.app/jobs/api  (best-effort; falls back silently)

Dedupe is by (source, source_job_id). Notifications are one-shot per match.
"""

import os
import re
import logging
import asyncio
import certifi
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pytz
import aiohttp
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)
EAT = pytz.timezone("Africa/Nairobi")

# ══════════════════════════════════════════════
# SKILL PROFILE — Daniel's CV-derived weights
# ══════════════════════════════════════════════
# Weighted skill groups. Each group can contribute up to its listed max.
# Keywords match case-insensitively against title + description + tags.
SKILL_GROUPS = {
    "design_core": {
        "max_points": 18,
        "keywords": [
            "figma", "ui/ux", "ui designer", "ux designer", "product designer",
            "visual designer", "brand identity", "branding", "design system",
        ],
    },
    "design_motion": {
        "max_points": 10,
        "keywords": [
            "motion graphics", "motion designer", "video editor", "video editing",
            "after effects", "premiere pro", "animation", "animator", "creative director",
        ],
    },
    "dev_python_llm": {
        "max_points": 18,
        "keywords": [
            "python", "llm", "large language model", "openai", "anthropic",
            "claude", "gemini", "gpt", "rag", "langchain", "mcp", "agent",
            "prompt engineer", "ml engineer", "ai engineer",
        ],
    },
    "dev_wordpress": {
        "max_points": 10,
        "keywords": [
            "wordpress", "wp plugin", "php", "elementor", "directorist",
            "buddyboss", "woocommerce",
        ],
    },
    "dev_fullstack": {
        "max_points": 10,
        "keywords": [
            "full stack", "full-stack", "fullstack", "mongodb", "react",
            "vue", "next.js", "node.js", "javascript", "typescript",
            "rest api", "flask", "fastapi", "discord.py",
        ],
    },
    "dev_integration": {
        "max_points": 6,
        "keywords": [
            "discord bot", "slack bot", "telegram bot", "koyeb", "vercel",
            "railway", "heroku", "docker", "ci/cd",
        ],
    },
}

# Skills match: up to 40 total (capped even if groups sum higher)
SKILLS_TOTAL_CAP = 40

# Keyword quality: senior/staff > mid > junior
SENIORITY_KEYWORDS = {
    "positive": ["senior", "staff", "lead", "principal", "consultant", "freelance"],
    "negative": ["intern", "internship", "graduate programme"],
}

# Currencies we can parse to USD. If salary is provided in KES or EUR etc,
# we convert with rough static rates (good enough for filtering).
CURRENCY_TO_USD_PER_MONTH = {
    "usd": 1.0,
    "eur": 1.08,
    "gbp": 1.27,
    "kes": 1 / 130.0,
}

# ══════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════
MIN_SALARY_USD_MONTH = 1200  # below this = 0 salary points
TARGET_SALARY_USD = 1500  # user's stated target
PREMIUM_SALARY_USD = 2000

DM_THRESHOLD = 70  # quality over quantity
DUPLICATE_DM_COOLDOWN_DAYS = 30  # don't re-notify same role

# User's Discord ID (read from env, no hard-code)
JOB_SCOUT_USER_ID = os.getenv("BOT_OWNER_ID") or os.getenv("DISCORD_OWNER_ID")

# ══════════════════════════════════════════════
# MONGODB
# ══════════════════════════════════════════════
_db = None
_jobs_col = None


def _get_db():
    """Lazy Mongo init, safe to call from any event loop."""
    global _db, _jobs_col
    if _db is not None:
        return _jobs_col
    try:
        mongo_uri = os.getenv("MONGO_URI")
        if not mongo_uri:
            logger.warning("job_scout: MONGO_URI not set, persistence disabled")
            return None
        client = MongoClient(
            mongo_uri, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=10000
        )
        _db = client["emily_brain_db"]
        _jobs_col = _db["job_matches"]
        # Dedupe + recency indexes
        _jobs_col.create_index(
            [("source", ASCENDING), ("source_id", ASCENDING)], unique=True
        )
        _jobs_col.create_index([("notified_at", DESCENDING)])
        _jobs_col.create_index([("score", DESCENDING)])
        logger.info("job_scout: MongoDB connected")
        return _jobs_col
    except PyMongoError as e:
        logger.error(f"job_scout Mongo init failed: {e}")
        return None


# ══════════════════════════════════════════════
# FETCHERS
# ══════════════════════════════════════════════
async def _fetch_json(session: aiohttp.ClientSession, url: str, timeout: int = 15):
    """GET a URL returning JSON, with sane headers. Returns None on failure."""
    try:
        headers = {
            "User-Agent": "EmilyJobScout/1.0 (personal use; contact owner via Discord)",
            "Accept": "application/json",
        }
        async with session.get(url, headers=headers, timeout=timeout) as r:
            if r.status != 200:
                logger.warning(f"job_scout fetch {url} returned {r.status}")
                return None
            return await r.json(content_type=None)
    except (asyncio.TimeoutError, aiohttp.ClientError, ValueError) as e:
        logger.warning(f"job_scout fetch error for {url}: {e}")
        return None


async def fetch_remoteok(session: aiohttp.ClientSession) -> List[Dict]:
    """RemoteOK API. First element is a metadata header, skip it."""
    data = await _fetch_json(session, "https://remoteok.com/api")
    if not data or not isinstance(data, list):
        return []
    jobs = []
    for item in data[1:]:  # skip legal header
        if not isinstance(item, dict):
            continue
        try:
            jobs.append(
                {
                    "source": "remoteok",
                    "source_id": str(item.get("id") or item.get("slug") or ""),
                    "title": (item.get("position") or item.get("title") or "").strip(),
                    "company": (item.get("company") or "").strip(),
                    "url": item.get("url") or item.get("apply_url") or "",
                    "description": (item.get("description") or "")[:3000],
                    "tags": [t.lower() for t in (item.get("tags") or []) if t],
                    "location": item.get("location") or "Remote",
                    "salary_min": _coerce_number(item.get("salary_min")),
                    "salary_max": _coerce_number(item.get("salary_max")),
                    "salary_currency": "usd",  # RemoteOK reports USD yearly
                    "salary_period": "year",
                    "posted_at": _parse_iso(item.get("date")),
                    "remote": True,
                }
            )
        except Exception as e:
            logger.debug(f"remoteok row skipped: {e}")
    return jobs


async def fetch_remotive(session: aiohttp.ClientSession) -> List[Dict]:
    """Remotive API. Returns {'jobs': [...]}."""
    data = await _fetch_json(session, "https://remotive.com/api/remote-jobs")
    if not data or "jobs" not in data:
        return []
    jobs = []
    for item in data.get("jobs", []):
        try:
            salary_text = (item.get("salary") or "").lower()
            smin, smax, scur, sper = _parse_salary_text(salary_text)
            jobs.append(
                {
                    "source": "remotive",
                    "source_id": str(item.get("id") or ""),
                    "title": (item.get("title") or "").strip(),
                    "company": (item.get("company_name") or "").strip(),
                    "url": item.get("url") or "",
                    "description": (item.get("description") or "")[:3000],
                    "tags": [t.lower() for t in (item.get("tags") or []) if t],
                    "location": item.get("candidate_required_location") or "Remote",
                    "salary_min": smin,
                    "salary_max": smax,
                    "salary_currency": scur,
                    "salary_period": sper,
                    "posted_at": _parse_iso(item.get("publication_date")),
                    "remote": True,
                }
            )
        except Exception as e:
            logger.debug(f"remotive row skipped: {e}")
    return jobs


async def fetch_arbeitnow(session: aiohttp.ClientSession) -> List[Dict]:
    """Arbeitnow API — mostly European + remote. Returns {'data': [...]}."""
    data = await _fetch_json(session, "https://www.arbeitnow.com/api/job-board-api")
    if not data or "data" not in data:
        return []
    jobs = []
    for item in data.get("data", []):
        try:
            tags = [t.lower() for t in (item.get("tags") or []) if t]
            # Keep only remote roles, skip on-site non-Kenya
            if not item.get("remote") and "remote" not in tags:
                continue
            jobs.append(
                {
                    "source": "arbeitnow",
                    "source_id": str(item.get("slug") or ""),
                    "title": (item.get("title") or "").strip(),
                    "company": (item.get("company_name") or "").strip(),
                    "url": item.get("url") or "",
                    "description": (item.get("description") or "")[:3000],
                    "tags": tags,
                    "location": item.get("location") or "Remote",
                    "salary_min": None,
                    "salary_max": None,
                    "salary_currency": "usd",
                    "salary_period": "year",
                    "posted_at": _parse_unix(item.get("created_at")),
                    "remote": True,
                }
            )
        except Exception as e:
            logger.debug(f"arbeitnow row skipped: {e}")
    return jobs


# ══════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════
def _coerce_number(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_iso(v) -> Optional[datetime]:
    if not v:
        return None
    try:
        s = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        return dt.astimezone(EAT)
    except (ValueError, TypeError):
        return None


def _parse_unix(v) -> Optional[datetime]:
    try:
        ts = int(v)
        return datetime.fromtimestamp(ts, tz=pytz.UTC).astimezone(EAT)
    except (TypeError, ValueError):
        return None


_NUM_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(k)?", re.IGNORECASE)


def _parse_salary_text(text: str) -> Tuple[Optional[float], Optional[float], str, str]:
    """Best-effort parser for free-text salary strings like '$80k - $120k', 'USD 2000-3000 per month',
    '€50k+', '60,000 - 80,000 EUR'.

    Returns (min, max, currency_code, period) where period is 'year' or 'month'.
    Returns (None, None, 'usd', 'year') if nothing parseable.
    """
    if not text:
        return None, None, "usd", "year"

    t = text.lower().replace(",", "")

    # Detect currency
    if "kes" in t or "ksh" in t:
        cur = "kes"
    elif "eur" in t or "€" in t:
        cur = "eur"
    elif "gbp" in t or "£" in t:
        cur = "gbp"
    else:
        cur = "usd"

    # Detect period
    period = "month" if re.search(r"\b(month|mo|per month|/mo)\b", t) else "year"

    # Find all numbers with optional 'k' suffix
    matches = _NUM_RE.findall(t)
    if not matches:
        return None, None, cur, period

    def to_val(num_str: str, k_flag: str) -> float:
        v = float(num_str)
        if k_flag or (v < 1000 and period == "year" and cur == "usd"):
            # If number is suspiciously small for an annual USD salary, assume 'k' was implied
            # e.g. "80 - 120" in context of yearly USD
            if k_flag:
                v *= 1000
        return v

    try:
        vals = [to_val(num, k) for num, k in matches]
        # Filter out obvious noise (tiny numbers that aren't salary)
        vals = [v for v in vals if v >= 100]
        if not vals:
            return None, None, cur, period
        if len(vals) >= 2:
            return vals[0], vals[1], cur, period
        return vals[0], vals[0], cur, period
    except (ValueError, IndexError):
        return None, None, cur, period


def _salary_to_monthly_usd(
    smin: Optional[float], smax: Optional[float], currency: str, period: str
) -> Tuple[Optional[float], Optional[float]]:
    """Normalize salary to USD/month range."""
    if smin is None and smax is None:
        return None, None
    rate = CURRENCY_TO_USD_PER_MONTH.get((currency or "usd").lower(), 1.0)
    divisor = 12 if period == "year" else 1

    def conv(v):
        if v is None:
            return None
        return (v * rate) / divisor

    return conv(smin), conv(smax)


# ══════════════════════════════════════════════
# CATEGORIZATION
# ══════════════════════════════════════════════
DESIGN_MARKERS = {
    "figma", "ui", "ux", "ui/ux", "visual", "brand", "branding", "designer",
    "motion", "video editor", "creative director", "illustrator", "photoshop",
    "after effects", "premiere",
}
DEV_MARKERS = {
    "python", "developer", "engineer", "backend", "frontend", "full-stack",
    "fullstack", "software", "wordpress", "php", "llm", "ai engineer",
    "ml engineer", "javascript", "typescript",
}


def categorize(title: str, description: str, tags: List[str]) -> str:
    """Return 'DESIGN', 'DEV', or 'HYBRID'.

    Uses word-boundary matching so short markers like 'ui' and 'ux' don't
    accidentally match substrings of unrelated words (e.g. 'build', 'tuxedo',
    'multi-user'). Tag matches are direct (tags are already tokenized).
    """
    text = " ".join([title or "", description or ""]).lower()
    tag_set = {t.lower() for t in (tags or [])}

    def has_marker(marker: str) -> bool:
        if marker in tag_set:
            return True
        # Word-boundary regex: marker surrounded by non-alphanumeric or string edges
        pattern = r"(?<![a-z0-9])" + re.escape(marker) + r"(?![a-z0-9])"
        return bool(re.search(pattern, text))

    design_hit = any(has_marker(m) for m in DESIGN_MARKERS)
    dev_hit = any(has_marker(m) for m in DEV_MARKERS)
    if design_hit and dev_hit:
        return "HYBRID"
    if design_hit:
        return "DESIGN"
    if dev_hit:
        return "DEV"
    return "DEV"  # default for ambiguous tech-ish roles


# ══════════════════════════════════════════════
# SCORING
# ══════════════════════════════════════════════
def score_job(job: Dict) -> Tuple[int, Dict]:
    """Return (score_0_to_100, detail_dict). Pure function, testable."""
    title = (job.get("title") or "").lower()
    desc = (job.get("description") or "").lower()
    tags = [t.lower() for t in (job.get("tags") or [])]
    haystack = " ".join([title, desc] + tags)

    # ── Skills (0-40) ──
    # Each matching keyword in a group contributes its group's per-hit points,
    # capped at the group's max. One strong hit = ~60% of group max; two or more
    # hits saturate it. This is simpler and more forgiving than the old formula.
    skill_points = 0
    matched_keywords = []
    for group_name, group in SKILL_GROUPS.items():
        group_hits = [kw for kw in group["keywords"] if kw.lower() in haystack]
        if group_hits:
            # Per-hit value: roughly 60% for one match, 100% for two+
            per_hit = group["max_points"] * 0.6
            gained = min(group["max_points"], round(per_hit * min(2, len(group_hits))))
            skill_points += gained
            matched_keywords.extend(group_hits[:3])
    skill_points = min(SKILLS_TOTAL_CAP, skill_points)

    # ── Salary (0-30) ──
    smin_m, smax_m = _salary_to_monthly_usd(
        job.get("salary_min"),
        job.get("salary_max"),
        job.get("salary_currency") or "usd",
        job.get("salary_period") or "year",
    )
    salary_pts = 0
    salary_basis = "unknown"
    if smin_m is not None or smax_m is not None:
        # Use midpoint if both present, else whichever is present
        if smin_m and smax_m:
            mid = (smin_m + smax_m) / 2
        else:
            mid = smin_m or smax_m
        salary_basis = f"~${mid:,.0f}/mo"
        if mid >= PREMIUM_SALARY_USD:
            salary_pts = 30
        elif mid >= TARGET_SALARY_USD:
            salary_pts = 25
        elif mid >= MIN_SALARY_USD_MONTH:
            salary_pts = 15
        else:
            salary_pts = 0
    else:
        # No salary info published — don't penalize hard, give benefit of doubt
        salary_pts = 10
        salary_basis = "not listed"

    # ── Remote/location (0-15) ──
    location = (job.get("location") or "").lower()
    remote_pts = 0
    if job.get("remote") or "remote" in location or "anywhere" in location or "worldwide" in location:
        remote_pts = 15
    elif "kenya" in location or "nairobi" in location:
        remote_pts = 13
    elif "africa" in location:
        remote_pts = 8
    elif "hybrid" in location:
        remote_pts = 5

    # ── Seniority / quality (0-15) ──
    quality_pts = 8  # baseline
    for kw in SENIORITY_KEYWORDS["positive"]:
        if kw in haystack:
            quality_pts += 2
            break
    for kw in SENIORITY_KEYWORDS["negative"]:
        if kw in haystack:
            quality_pts = max(0, quality_pts - 8)
            break
    if "consultant" in haystack or "freelance" in haystack:
        quality_pts += 3
    quality_pts = min(15, quality_pts)

    total = skill_points + salary_pts + remote_pts + quality_pts
    total = min(100, total)

    detail = {
        "skills_pts": skill_points,
        "salary_pts": salary_pts,
        "remote_pts": remote_pts,
        "quality_pts": quality_pts,
        "matched_keywords": list(set(matched_keywords))[:8],
        "salary_basis": salary_basis,
        "salary_monthly_usd_mid": (
            round((smin_m + smax_m) / 2) if (smin_m and smax_m) else None
        ),
    }
    return total, detail


# ══════════════════════════════════════════════
# PERSISTENCE
# ══════════════════════════════════════════════
def is_job_seen(source: str, source_id: str) -> bool:
    col = _get_db()
    if col is None:
        return False
    try:
        return col.find_one({"source": source, "source_id": source_id}, {"_id": 1}) is not None
    except PyMongoError:
        return False


def upsert_job(job: Dict, score: int, detail: Dict, category: str) -> None:
    col = _get_db()
    if col is None:
        return
    try:
        now = datetime.now(EAT)
        col.update_one(
            {"source": job["source"], "source_id": job["source_id"]},
            {
                "$set": {
                    **job,
                    "score": score,
                    "score_detail": detail,
                    "category": category,
                    "updated_at": now,
                },
                "$setOnInsert": {"discovered_at": now, "user_reaction": None, "notified_at": None},
            },
            upsert=True,
        )
    except PyMongoError as e:
        logger.error(f"upsert_job failed: {e}")


def mark_notified(source: str, source_id: str) -> None:
    col = _get_db()
    if col is None:
        return
    try:
        col.update_one(
            {"source": source, "source_id": source_id},
            {"$set": {"notified_at": datetime.now(EAT)}},
        )
    except PyMongoError as e:
        logger.error(f"mark_notified failed: {e}")


def mark_reaction(source: str, source_id: str, reaction: str) -> bool:
    """reaction: 'applied' | 'skipped' | None"""
    col = _get_db()
    if col is None:
        return False
    try:
        r = col.update_one(
            {"source": source, "source_id": source_id},
            {"$set": {"user_reaction": reaction, "reacted_at": datetime.now(EAT)}},
        )
        return r.modified_count > 0
    except PyMongoError as e:
        logger.error(f"mark_reaction failed: {e}")
        return False


def get_top_matches(days: int = 1, limit: int = 5, min_score: int = 0) -> List[Dict]:
    col = _get_db()
    if col is None:
        return []
    try:
        cutoff = datetime.now(EAT) - timedelta(days=days)
        cursor = (
            col.find(
                {"discovered_at": {"$gte": cutoff}, "score": {"$gte": min_score}},
                {"_id": 0},
            )
            .sort("score", -1)
            .limit(limit)
        )
        return list(cursor)
    except PyMongoError as e:
        logger.error(f"get_top_matches failed: {e}")
        return []


def get_matches_by_category(category: str, days: int = 7, limit: int = 5) -> List[Dict]:
    col = _get_db()
    if col is None:
        return []
    try:
        cutoff = datetime.now(EAT) - timedelta(days=days)
        cursor = (
            col.find(
                {
                    "category": category.upper(),
                    "discovered_at": {"$gte": cutoff},
                    "score": {"$gte": DM_THRESHOLD - 10},  # a bit broader for browse
                },
                {"_id": 0},
            )
            .sort("score", -1)
            .limit(limit)
        )
        return list(cursor)
    except PyMongoError as e:
        logger.error(f"get_matches_by_category failed: {e}")
        return []


def get_jobs_needing_dm(threshold: int = DM_THRESHOLD, limit: int = 3, lookback_hours: int = 168) -> List[Dict]:
    """Return top unnotified jobs at/above threshold, discovered within lookback window.

    Default lookback is 168h (7 days) to match the weekly scout schedule. Pass
    lookback_hours=24 if you switch back to daily scouting.
    """
    col = _get_db()
    if col is None:
        return []
    try:
        cutoff = datetime.now(EAT) - timedelta(hours=lookback_hours)
        cursor = (
            col.find(
                {
                    "score": {"$gte": threshold},
                    "notified_at": None,
                    "discovered_at": {"$gte": cutoff},
                },
                {"_id": 0},
            )
            .sort("score", -1)
            .limit(limit)
        )
        return list(cursor)
    except PyMongoError as e:
        logger.error(f"get_jobs_needing_dm failed: {e}")
        return []


# ══════════════════════════════════════════════
# SCOUT ORCHESTRATOR
# ══════════════════════════════════════════════
async def run_scout() -> Dict:
    """Fetch all sources, score, persist. Returns summary stats."""
    stats = {"fetched": 0, "new": 0, "scored_high": 0, "errors": []}

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        results = await asyncio.gather(
            fetch_remoteok(session),
            fetch_remotive(session),
            fetch_arbeitnow(session),
            return_exceptions=True,
        )

    all_jobs = []
    for r in results:
        if isinstance(r, Exception):
            stats["errors"].append(str(r))
            continue
        all_jobs.extend(r)

    stats["fetched"] = len(all_jobs)

    for job in all_jobs:
        if not job.get("source_id") or not job.get("title"):
            continue
        try:
            # Skip if already seen (we still re-score on updates but don't count as new)
            already = is_job_seen(job["source"], job["source_id"])
            score, detail = score_job(job)
            category = categorize(job["title"], job["description"], job["tags"])
            upsert_job(job, score, detail, category)
            if not already:
                stats["new"] += 1
                if score >= DM_THRESHOLD:
                    stats["scored_high"] += 1
        except Exception as e:
            logger.error(f"scoring failed for {job.get('source_id')}: {e}")

    return stats


# ══════════════════════════════════════════════
# DISCORD DM FORMATTER
# ══════════════════════════════════════════════
CATEGORY_EMOJI = {"DESIGN": "🎨", "DEV": "💻", "HYBRID": "🎨💻"}


def format_job_dm(job: Dict) -> str:
    """Render a single job as a Discord DM message."""
    cat = job.get("category", "DEV")
    score = job.get("score", 0)
    detail = job.get("score_detail", {}) or {}

    title = job.get("title", "Untitled")
    company = job.get("company") or "Unknown company"
    location = job.get("location") or "Remote"
    salary_basis = detail.get("salary_basis", "not listed")
    matched = detail.get("matched_keywords", [])

    reasons = []
    if matched:
        reasons.append(f"✓ Matched skills: {', '.join(matched[:5])}")
    if detail.get("salary_pts", 0) >= 25:
        reasons.append(f"✓ Salary above your target ({salary_basis})")
    elif detail.get("salary_pts", 0) >= 15:
        reasons.append(f"✓ Salary meets floor ({salary_basis})")
    if detail.get("remote_pts", 0) >= 13:
        reasons.append("✓ Remote or Kenya-based")
    reason_block = "\n".join(reasons) if reasons else ""

    url = job.get("url") or "(no link)"

    lines = [
        f"💼 **New match** · {score}/100 {CATEGORY_EMOJI.get(cat, '')} *{cat}*",
        "",
        f"**{title}**",
        f"{company} · {location}",
        f"Salary: {salary_basis}",
        "",
        reason_block,
        "",
        f"🔗 {url}",
        "",
        "_React ✅ if you apply, ❌ if the match was off._",
    ]
    return "\n".join(filter(None, lines))


def format_digest(matches: List[Dict], title_line: str = "Today's top matches") -> str:
    """Compact digest of multiple matches."""
    if not matches:
        return "No new matches yet today. I'll keep scouting."
    lines = [f"💼 **{title_line}**", ""]
    for i, m in enumerate(matches, 1):
        cat = m.get("category", "DEV")
        emoji = CATEGORY_EMOJI.get(cat, "")
        lines.append(
            f"**{i}. {m.get('title', '')[:60]}** · {m.get('company', '')[:30]} "
            f"— {m.get('score', 0)}/100 {emoji}"
        )
        lines.append(f"   {m.get('url', '')}")
    return "\n".join(lines)

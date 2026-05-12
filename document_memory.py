"""
document_memory.py — Long-term reference document storage for Emily.

Lets the user save named documents (PDFs, text, code, etc) to Emily's
permanent memory and "pin" them to projects. While a document is pinned,
every Claude/Gemini call automatically includes its contents as context,
so Emily can answer questions about it weeks or months later.

Workflow:
1. User uploads a PDF + types `!savedoc tabasamu` — Emily extracts text,
   generates a short summary, stores everything in MongoDB.
2. User types `!pin tabasamu 30` — pinned for the next 30 days.
3. For 30 days, every AI response gets the doc's text injected as
   reference context. Emily can answer "what's the primary color?"
   weeks after the upload, exactly as if the PDF was still attached.
4. When the project's done: `!unpin tabasamu`. Document remains saved.
5. A year later: `!pin tabasamu` re-activates it.

This is option 2 of the memory-expansion ladder we discussed — full RAG
with embeddings would be option 3, and isn't needed for project-scoped
work like a month-long branding job.
"""

import os
import io
import logging
import certifi
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pytz
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import PyMongoError, DuplicateKeyError

logger = logging.getLogger(__name__)
EAT = pytz.timezone("Africa/Nairobi")

# ══════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════
# Cap on the text we'll store per doc — protects MongoDB and per-reply token cost
MAX_DOC_CHARS = 60_000          # ~15K tokens. Roughly 100 pages of text.
MAX_DOCS_PER_USER = 25          # Sanity cap on how many docs one user can save
DEFAULT_PIN_DAYS = 30           # Default duration when user just types `!pin name`
MAX_PIN_DAYS = 365              # Hard limit on pin duration
NAME_PATTERN = r"^[a-z0-9_-]{2,40}$"  # Slug for !savedoc / !pin args

# ══════════════════════════════════════════════
# MONGODB
# ══════════════════════════════════════════════
_db = None
_docs_col = None
_pins_col = None


def _get_collections():
    """Lazy Mongo init, returns (docs_col, pins_col)."""
    global _db, _docs_col, _pins_col
    if _db is not None:
        return _docs_col, _pins_col
    try:
        mongo_uri = os.getenv("MONGO_URI")
        if not mongo_uri:
            logger.warning("document_memory: MONGO_URI not set, persistence disabled")
            return None, None
        client = MongoClient(
            mongo_uri, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=10000
        )
        _db = client["emily_brain_db"]
        _docs_col = _db["saved_documents"]
        _pins_col = _db["document_pins"]
        # Indexes
        _docs_col.create_index(
            [("user_id", ASCENDING), ("name", ASCENDING)], unique=True
        )
        _docs_col.create_index([("user_id", ASCENDING), ("saved_at", DESCENDING)])
        _pins_col.create_index(
            [("user_id", ASCENDING), ("doc_name", ASCENDING)], unique=True
        )
        _pins_col.create_index([("user_id", ASCENDING), ("expires_at", ASCENDING)])
        logger.info("document_memory: MongoDB connected")
        return _docs_col, _pins_col
    except PyMongoError as e:
        logger.error(f"document_memory Mongo init failed: {e}")
        return None, None


# ══════════════════════════════════════════════
# VALIDATION
# ══════════════════════════════════════════════
def _validate_name(name: str) -> Tuple[bool, str]:
    """Returns (is_valid, error_message). Validates the raw input — does not lowercase."""
    import re as _re
    if not name or not name.strip():
        return False, "Name can't be empty."
    stripped = name.strip()
    if not _re.match(NAME_PATTERN, stripped):
        return False, (
            "Name must be 2-40 characters, lowercase letters/digits/hyphens/underscores only. "
            "Example: `tabasamu`, `client-acme`, `personal_brand_2026`."
        )
    return True, ""


def normalize_name(name: str) -> str:
    """Lowercases and strips a doc name. Use everywhere we accept input."""
    return (name or "").strip().lower()


# ══════════════════════════════════════════════
# SAVE / DELETE / LIST
# ══════════════════════════════════════════════
def save_document(
    user_id: str,
    name: str,
    content: str,
    source: str = "unknown",
    summary: str = "",
    original_filename: str = "",
) -> Tuple[bool, str]:
    """Save a document to a user's permanent memory under `name`.

    Returns (success, message). Truncates content to MAX_DOC_CHARS. If a doc
    with the same name already exists, this OVERWRITES it (the user typed
    `!savedoc name` again — that's an intentional update).
    """
    docs_col, _ = _get_collections()
    if docs_col is None:
        return False, "Database unavailable. Try again in a bit."

    ok, err = _validate_name(name)
    if not ok:
        return False, err

    if not content or not content.strip():
        return False, "Document content is empty — nothing to save."

    name = normalize_name(name)
    user_id = str(user_id)

    # Enforce per-user cap
    try:
        existing_count = docs_col.count_documents({"user_id": user_id})
        existing = docs_col.find_one({"user_id": user_id, "name": name})
        if existing is None and existing_count >= MAX_DOCS_PER_USER:
            return False, (
                f"You already have {MAX_DOCS_PER_USER} saved documents (the max). "
                f"Use `!deldoc <name>` to free up space."
            )
    except PyMongoError as e:
        logger.error(f"save_document count failed: {e}")
        return False, "Couldn't check your existing docs. Try again."

    # Truncate if needed
    truncated = False
    original_len = len(content)
    if original_len > MAX_DOC_CHARS:
        content = content[:MAX_DOC_CHARS] + f"\n\n... (truncated, original was {original_len:,} chars)"
        truncated = True

    now = datetime.now(EAT)
    try:
        docs_col.update_one(
            {"user_id": user_id, "name": name},
            {
                "$set": {
                    "user_id": user_id,
                    "name": name,
                    "content": content,
                    "summary": summary[:500] if summary else "",
                    "source": source,
                    "original_filename": original_filename,
                    "char_count": len(content),
                    "updated_at": now,
                },
                "$setOnInsert": {"saved_at": now},
            },
            upsert=True,
        )
        verb = "updated" if existing else "saved"
        msg = f"✅ {verb.capitalize()} **{name}** ({len(content):,} chars)."
        if truncated:
            msg += f"\n_Truncated from {original_len:,} chars — large docs are capped at {MAX_DOC_CHARS:,}._"
        return True, msg
    except PyMongoError as e:
        logger.error(f"save_document failed: {e}")
        return False, "Couldn't save. Try again."


def delete_document(user_id: str, name: str) -> Tuple[bool, str]:
    """Delete a saved document. Also removes any pin on it."""
    docs_col, pins_col = _get_collections()
    if docs_col is None:
        return False, "Database unavailable."

    name = normalize_name(name)
    user_id = str(user_id)

    try:
        r = docs_col.delete_one({"user_id": user_id, "name": name})
        if r.deleted_count == 0:
            return False, f"No document named **{name}** found."
        # Clean up any pin
        if pins_col is not None:
            pins_col.delete_one({"user_id": user_id, "doc_name": name})
        return True, f"🗑️ Deleted **{name}** and any pin on it."
    except PyMongoError as e:
        logger.error(f"delete_document failed: {e}")
        return False, "Couldn't delete. Try again."


def get_document(user_id: str, name: str) -> Optional[Dict]:
    """Fetch a single saved doc by name. Returns None if not found."""
    docs_col, _ = _get_collections()
    if docs_col is None:
        return None
    try:
        return docs_col.find_one(
            {"user_id": str(user_id), "name": normalize_name(name)},
            {"_id": 0},
        )
    except PyMongoError as e:
        logger.error(f"get_document failed: {e}")
        return None


def list_documents(user_id: str) -> List[Dict]:
    """List all of a user's saved docs (without their full content — just metadata)."""
    docs_col, _ = _get_collections()
    if docs_col is None:
        return []
    try:
        cursor = docs_col.find(
            {"user_id": str(user_id)},
            {
                "_id": 0,
                "name": 1,
                "summary": 1,
                "source": 1,
                "original_filename": 1,
                "char_count": 1,
                "saved_at": 1,
                "updated_at": 1,
            },
        ).sort("updated_at", DESCENDING)
        return list(cursor)
    except PyMongoError as e:
        logger.error(f"list_documents failed: {e}")
        return []


# ══════════════════════════════════════════════
# PIN / UNPIN
# ══════════════════════════════════════════════
def pin_document(user_id: str, name: str, days: int = DEFAULT_PIN_DAYS) -> Tuple[bool, str]:
    """Pin a document so it's auto-injected into AI context for N days."""
    docs_col, pins_col = _get_collections()
    if docs_col is None or pins_col is None:
        return False, "Database unavailable."

    name = normalize_name(name)
    user_id = str(user_id)

    # Validate the doc exists
    doc = get_document(user_id, name)
    if not doc:
        return False, f"No document named **{name}** — save it first with `!savedoc {name}` (attach a file)."

    # Clamp days
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = DEFAULT_PIN_DAYS
    days = max(1, min(MAX_PIN_DAYS, days))

    now = datetime.now(EAT)
    expires_at = now + timedelta(days=days)

    try:
        pins_col.update_one(
            {"user_id": user_id, "doc_name": name},
            {
                "$set": {
                    "user_id": user_id,
                    "doc_name": name,
                    "pinned_at": now,
                    "expires_at": expires_at,
                }
            },
            upsert=True,
        )
        return True, (
            f"📌 Pinned **{name}** for {days} day{'s' if days != 1 else ''}.\n"
            f"_Emily will reference it automatically until {expires_at.strftime('%a %d %b %Y')}._\n"
            f"_Use `!unpin {name}` to remove early._"
        )
    except PyMongoError as e:
        logger.error(f"pin_document failed: {e}")
        return False, "Couldn't pin. Try again."


def unpin_document(user_id: str, name: str) -> Tuple[bool, str]:
    """Remove a pin without deleting the document itself."""
    _, pins_col = _get_collections()
    if pins_col is None:
        return False, "Database unavailable."

    name = normalize_name(name)
    user_id = str(user_id)
    try:
        r = pins_col.delete_one({"user_id": user_id, "doc_name": name})
        if r.deleted_count == 0:
            return False, f"**{name}** wasn't pinned."
        return True, (
            f"📍 Unpinned **{name}**. The document is still saved — "
            f"use `!pin {name}` anytime to re-activate it."
        )
    except PyMongoError as e:
        logger.error(f"unpin_document failed: {e}")
        return False, "Couldn't unpin. Try again."


def get_active_pins(user_id: str) -> List[Dict]:
    """Return pins that haven't expired yet. Auto-removes expired pins."""
    _, pins_col = _get_collections()
    if pins_col is None:
        return []
    now = datetime.now(EAT)
    user_id = str(user_id)
    try:
        # Garbage-collect expired pins lazily
        pins_col.delete_many({"user_id": user_id, "expires_at": {"$lt": now}})
        return list(
            pins_col.find(
                {"user_id": user_id, "expires_at": {"$gte": now}}, {"_id": 0}
            ).sort("pinned_at", DESCENDING)
        )
    except PyMongoError as e:
        logger.error(f"get_active_pins failed: {e}")
        return []


# ══════════════════════════════════════════════
# AI CONTEXT INJECTION
# ══════════════════════════════════════════════
def build_pinned_context_block(user_id: str, max_chars: int = 40_000) -> Optional[str]:
    """Build the system-prompt fragment that gets injected when there are active pins.

    Returns None if no active pins (so caller can skip cleanly). The returned
    string is meant to be appended to Emily's system prompt — it tells her
    these docs are reference material she should consult when relevant.

    max_chars protects against blowing the context window — if the user has
    multiple large pinned docs, we trim by oldest pin first.
    """
    pins = get_active_pins(user_id)
    if not pins:
        return None

    docs_col, _ = _get_collections()
    if docs_col is None:
        return None

    blocks = []
    total = 0
    user_id = str(user_id)

    for pin in pins:
        try:
            doc = docs_col.find_one(
                {"user_id": user_id, "name": pin["doc_name"]},
                {"_id": 0, "name": 1, "content": 1, "summary": 1, "original_filename": 1},
            )
            if not doc:
                continue
            content = doc.get("content") or ""
            if not content.strip():
                continue
            header = f"📌 **{doc['name']}**"
            if doc.get("original_filename"):
                header += f" (from {doc['original_filename']})"
            block = f"{header}\n{content}\n"
            if total + len(block) > max_chars:
                # Trim this doc to fit, then stop
                remaining = max_chars - total - len(header) - 50
                if remaining > 500:
                    blocks.append(f"{header}\n{content[:remaining]}\n... (trimmed to fit context)\n")
                break
            blocks.append(block)
            total += len(block)
        except PyMongoError as e:
            logger.error(f"build_pinned_context_block: {e}")
            continue

    if not blocks:
        return None

    return (
        "\n\n═══════════════════════════════════════\n"
        "PINNED REFERENCE DOCUMENTS:\n"
        "═══════════════════════════════════════\n"
        "The user has pinned these documents as reference material for ongoing work. "
        "When their questions relate to one of these, draw on the document's content directly. "
        "Don't proactively recite from a doc unless asked — just have it available so you can "
        "answer accurately when the topic comes up. Don't mention you're 'reading from a doc' — "
        "just speak about the content naturally, the way someone who's read it would.\n\n"
        + "\n---\n".join(blocks)
        + "\n═══════════════════════════════════════\n"
    )


def get_pin_indicator(user_id: str) -> str:
    """Short suffix to append to a reply, showing which docs are pinned.

    Empty string if no pins. Format: `\n\n📌 *tabasamu, client-acme*`
    """
    pins = get_active_pins(user_id)
    if not pins:
        return ""
    names = [p["doc_name"] for p in pins[:3]]
    suffix = ", ".join(names)
    if len(pins) > 3:
        suffix += f" +{len(pins) - 3}"
    return f"\n\n📌 *{suffix}*"


# ══════════════════════════════════════════════
# FORMATTERS (for Discord output)
# ══════════════════════════════════════════════
def format_docs_list(docs: List[Dict], pins: List[Dict]) -> str:
    """Render `!docs` output as a Discord message."""
    if not docs:
        return (
            "📚 You haven't saved any documents yet.\n"
            "_Attach a file and type `!savedoc <name>` to save one._"
        )

    pinned_names = {p["doc_name"] for p in pins}
    lines = ["📚 **Your saved documents**\n"]
    for d in docs:
        is_pinned = d["name"] in pinned_names
        marker = "📌" if is_pinned else "  "
        size = f"{d.get('char_count', 0):,} chars"
        ts = d.get("updated_at") or d.get("saved_at")
        date = ts.strftime("%d %b %Y") if ts else "—"
        line = f"{marker} **{d['name']}** · {size} · saved {date}"
        if d.get("original_filename"):
            line += f"\n      _{d['original_filename']}_"
        if d.get("summary"):
            line += f"\n      _{d['summary'][:120]}_"
        lines.append(line)

    lines.append("")
    if pins:
        lines.append(f"📌 _{len(pins)} document{'s' if len(pins) != 1 else ''} currently pinned_")
    lines.append("_Tap any one: `!doc <name>` for details, `!pin <name>` to activate._")
    return "\n".join(lines)


def format_doc_detail(doc: Dict, is_pinned: bool, pin_info: Optional[Dict] = None) -> str:
    """Render `!doc <name>` output."""
    if not doc:
        return "No such document."
    lines = [f"📄 **{doc['name']}**"]
    if doc.get("original_filename"):
        lines.append(f"_{doc['original_filename']}_")
    lines.append(f"Saved: {doc.get('saved_at').strftime('%d %b %Y') if doc.get('saved_at') else '—'}")
    if doc.get("updated_at") and doc.get("updated_at") != doc.get("saved_at"):
        lines.append(f"Updated: {doc['updated_at'].strftime('%d %b %Y')}")
    lines.append(f"Size: {doc.get('char_count', 0):,} chars")
    lines.append(f"Source: {doc.get('source', 'unknown')}")
    if is_pinned and pin_info:
        expires = pin_info.get("expires_at")
        if expires:
            days_left = (expires - datetime.now(EAT)).days
            lines.append(f"📌 **Pinned** — active for {days_left} more day{'s' if days_left != 1 else ''}")
    else:
        lines.append("📍 Not pinned — use `!pin " + doc["name"] + "` to activate")

    if doc.get("summary"):
        lines.append("")
        lines.append("**Summary:**")
        lines.append(doc["summary"])

    # Preview first 400 chars of content
    content = doc.get("content", "")
    if content:
        lines.append("")
        lines.append("**Preview:**")
        preview = content[:400].replace("\n", " ").strip()
        lines.append(f"_{preview}..._" if len(content) > 400 else f"_{preview}_")

    return "\n".join(lines)


# ══════════════════════════════════════════════
# TEXT EXTRACTION (for !savedoc with attached file)
# ══════════════════════════════════════════════
def extract_text_from_pdf_bytes(data: bytes, max_pages: int = 100) -> Optional[str]:
    """Pull text out of a PDF byte blob using pypdf. Returns None on failure."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        chunks = []
        for i, page in enumerate(reader.pages):
            if i >= max_pages:
                break
            try:
                txt = page.extract_text() or ""
                if txt.strip():
                    chunks.append(f"--- Page {i + 1} ---\n{txt.strip()}")
            except Exception as e:
                logger.warning(f"savedoc PDF page {i+1} extract failed: {e}")
                continue
        text = "\n\n".join(chunks)
        return text if text.strip() else None
    except ImportError:
        logger.error("pypdf not installed — can't extract PDF for !savedoc")
        return None
    except Exception as e:
        logger.error(f"PDF extraction failed in document_memory: {e}")
        return None


def extract_text_from_attachment(attachment_data: bytes, filename: str) -> Tuple[Optional[str], str]:
    """Extract text from a Discord attachment based on file type.

    Returns (text, source_label). text is None on failure.
    Supports: PDF, plain text (.txt, .md, .csv, .json, .py, etc).
    """
    ext = (filename.rsplit(".", 1)[-1].lower() if "." in filename else "").strip()
    if ext == "pdf":
        text = extract_text_from_pdf_bytes(attachment_data)
        return text, "pdf"
    # Treat everything else as utf-8 text
    try:
        text = attachment_data.decode("utf-8", errors="replace")
        return text, ext or "text"
    except Exception as e:
        logger.error(f"extract_text_from_attachment failed: {e}")
        return None, "unknown"

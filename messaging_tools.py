import os
import logging
from datetime import datetime
from dotenv import load_dotenv
import pytz
import certifi
from pymongo import MongoClient, ASCENDING
from pymongo.errors import PyMongoError

load_dotenv()
logger = logging.getLogger(__name__)

EAT_ZONE = pytz.timezone('Africa/Nairobi')

# --- AFRICA'S TALKING CONFIG ---
AT_USERNAME = os.getenv("AT_USERNAME", "sandbox")  # 'sandbox' for testing, your username for live
AT_API_KEY = os.getenv("AT_API_KEY")
AT_SENDER_ID = os.getenv("AT_SENDER_ID", "")  # Optional: custom sender ID for live
AT_ENVIRONMENT = os.getenv("AT_ENVIRONMENT", "sandbox")  # 'sandbox' or 'production'

# --- META WHATSAPP CLOUD API CONFIG ---
WA_PHONE_ID = os.getenv("WA_PHONE_NUMBER_ID")      # From Meta App Dashboard → WhatsApp → API Setup
WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN")       # Permanent token from System User
WA_API_VERSION = os.getenv("WA_API_VERSION", "v21.0")

# --- MONGODB ---
db = None
contacts_col = None
msg_log_col = None

try:
    mongo_client = MongoClient(
        os.getenv("MONGO_URI"),
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=5000,
    )
    mongo_client.admin.command('ping')
    db = mongo_client["emily_brain_db"]
    contacts_col = db["watchparty_contacts"]
    msg_log_col = db["message_log"]

    contacts_col.create_index([("guild_id", ASCENDING), ("phone", ASCENDING)], unique=True)
    contacts_col.create_index([("guild_id", ASCENDING)])
    msg_log_col.create_index([("sent_at", ASCENDING)])

    logger.info("Messaging tools connected to MongoDB!")
except Exception as e:
    logger.error(f"Messaging MongoDB error: {e}")


def _now():
    return datetime.now(EAT_ZONE)


def is_configured():
    """Check if Africa's Talking credentials are set."""
    return bool(AT_API_KEY)


# ══════════════════════════════════════════════
# SEND SMS (Africa's Talking)
# ══════════════════════════════════════════════
def send_sms(phone_number, message):
    """Send an SMS via Africa's Talking.
    phone_number: format '+254712345678'
    message: text string
    """
    if not is_configured():
        logger.warning("Africa's Talking not configured")
        return False, "SMS not configured"

    try:
        import africastalking
        africastalking.initialize(AT_USERNAME, AT_API_KEY)
        sms = africastalking.SMS

        kwargs = {
            "message": message,
            "recipients": [phone_number],
        }
        # Add sender ID for production (not needed in sandbox)
        if AT_SENDER_ID and AT_ENVIRONMENT == "production":
            kwargs["sender_id"] = AT_SENDER_ID

        response = sms.send(**kwargs)
        logger.info(f"SMS sent to {phone_number}: {response}")

        # Check response
        recipients = response.get("SMSMessageData", {}).get("Recipients", [])
        if recipients:
            status = recipients[0].get("status", "")
            if status == "Success":
                return True, "Sent"
            else:
                return False, f"SMS failed: {status}"

        return True, "Sent (no receipt)"

    except Exception as e:
        logger.error(f"SMS send error: {e}")
        return False, str(e)


def send_sms_batch(contacts, message_func):
    """Send personalized SMS to multiple contacts.
    contacts: list of dicts with 'phone' and 'name' keys
    message_func: callable(name) -> str that generates a unique message per person
    """
    results = {"sent": 0, "failed": 0, "errors": []}

    for contact in contacts:
        phone = contact.get("phone", "")
        name = contact.get("name", "Friend")

        if not phone:
            results["failed"] += 1
            results["errors"].append(f"No phone for {name}")
            continue

        # Generate unique message for this person
        message = message_func(name)

        success, detail = send_sms(phone, message)
        if success:
            results["sent"] += 1
            # Log the message
            _log_message(phone, name, message, "sms", success)
        else:
            results["failed"] += 1
            results["errors"].append(f"{name}: {detail}")
            _log_message(phone, name, message, "sms", False)

    return results


# ══════════════════════════════════════════════
# SEND WHATSAPP (Meta Cloud API)
# ══════════════════════════════════════════════
def wa_configured():
    """Check if Meta WhatsApp Cloud API is set up."""
    return bool(WA_PHONE_ID and WA_ACCESS_TOKEN)


def send_whatsapp(phone_number, message):
    """Send a WhatsApp text message via Meta Cloud API.
    phone_number: format '+254712345678' or '254712345678'
    message: text string (max ~4096 chars)
    """
    if not wa_configured():
        logger.warning("WhatsApp not configured — falling back to SMS")
        return send_sms(phone_number, message)

    try:
        import requests

        # Strip + prefix — Meta API expects just digits
        to_number = phone_number.lstrip("+")

        url = f"https://graph.facebook.com/{WA_API_VERSION}/{WA_PHONE_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_number,
            "type": "text",
            "text": {"body": message},
        }

        response = requests.post(url, headers=headers, json=payload, timeout=15)
        data = response.json()

        if response.status_code in (200, 201):
            msg_id = data.get("messages", [{}])[0].get("id", "unknown")
            logger.info(f"WhatsApp sent to {phone_number}: {msg_id}")
            return True, "Sent"
        else:
            error = data.get("error", {})
            error_msg = error.get("message", response.text[:200])
            error_code = error.get("code", response.status_code)
            logger.error(f"WhatsApp send failed [{error_code}]: {error_msg}")
            return False, f"WhatsApp failed: {error_msg}"

    except Exception as e:
        logger.error(f"WhatsApp send error: {e}")
        return False, str(e)


def send_whatsapp_template(phone_number, template_name="hello_world", language="en_US"):
    """Send a pre-approved WhatsApp template message.
    Required for first contact — you can't send free-form text to users
    who haven't messaged you in the last 24 hours.
    """
    if not wa_configured():
        return False, "WhatsApp not configured"

    try:
        import requests

        to_number = phone_number.lstrip("+")

        url = f"https://graph.facebook.com/{WA_API_VERSION}/{WA_PHONE_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
            },
        }

        response = requests.post(url, headers=headers, json=payload, timeout=15)
        data = response.json()

        if response.status_code in (200, 201):
            msg_id = data.get("messages", [{}])[0].get("id", "unknown")
            logger.info(f"WhatsApp template sent to {phone_number}: {msg_id}")
            return True, "Sent"
        else:
            error = data.get("error", {})
            error_msg = error.get("message", response.text[:200])
            logger.error(f"WhatsApp template failed: {error_msg}")
            return False, f"WhatsApp template failed: {error_msg}"

    except Exception as e:
        logger.error(f"WhatsApp template error: {e}")
        return False, str(e)


# ══════════════════════════════════════════════
# CONTACT MANAGEMENT (MongoDB)
# ══════════════════════════════════════════════
def add_contact(guild_id, name, phone):
    """Add a watch party contact."""
    if contacts_col is None:
        return False
    try:
        # Normalize phone number
        phone = _normalize_phone(phone)
        if not phone:
            return False

        contacts_col.update_one(
            {"guild_id": str(guild_id), "phone": phone},
            {"$set": {
                "guild_id": str(guild_id),
                "name": name,
                "phone": phone,
                "added_at": _now(),
            }},
            upsert=True,
        )
        logger.info(f"Contact added: {name} ({phone}) for guild {guild_id}")
        return True
    except PyMongoError as e:
        logger.error(f"Add contact error: {e}")
        return False


def remove_contact(guild_id, phone):
    """Remove a watch party contact."""
    if contacts_col is None:
        return False
    try:
        phone = _normalize_phone(phone)
        result = contacts_col.delete_one({
            "guild_id": str(guild_id),
            "phone": phone,
        })
        return result.deleted_count > 0
    except PyMongoError as e:
        logger.error(f"Remove contact error: {e}")
        return False


def remove_contact_by_name(guild_id, name):
    """Remove a watch party contact by name."""
    if contacts_col is None:
        return False
    try:
        result = contacts_col.delete_one({
            "guild_id": str(guild_id),
            "name": {"$regex": f"^{name}$", "$options": "i"},
        })
        return result.deleted_count > 0
    except PyMongoError as e:
        logger.error(f"Remove contact by name error: {e}")
        return False


def get_contacts(guild_id):
    """Get all watch party contacts for a server."""
    if contacts_col is None:
        return []
    try:
        return list(contacts_col.find(
            {"guild_id": str(guild_id)},
            {"_id": 0, "name": 1, "phone": 1, "added_at": 1}
        ).sort("name", 1))
    except PyMongoError as e:
        logger.error(f"Get contacts error: {e}")
        return []


def _normalize_phone(phone):
    """Normalize a Kenyan phone number to +254 format."""
    phone = phone.strip().replace(" ", "").replace("-", "")

    # Remove any non-digit characters except leading +
    if phone.startswith("+"):
        cleaned = "+" + "".join(c for c in phone[1:] if c.isdigit())
    else:
        cleaned = "".join(c for c in phone if c.isdigit())

    # Handle different formats
    if cleaned.startswith("+254"):
        return cleaned  # Already correct
    elif cleaned.startswith("254"):
        return f"+{cleaned}"
    elif cleaned.startswith("0") and len(cleaned) == 10:
        return f"+254{cleaned[1:]}"
    elif len(cleaned) == 9:
        return f"+254{cleaned}"
    else:
        # Try to use as-is with + prefix if it looks international
        if len(cleaned) >= 10:
            return f"+{cleaned}"
        return None  # Invalid


def format_contacts(contacts):
    """Format contact list for Discord."""
    if not contacts:
        return "No watch party contacts saved! Add some with `!addphone <name> <number>`"

    lines = ["📱 **Watch Party Contacts**\n"]
    for i, c in enumerate(contacts, 1):
        # Mask phone number for privacy
        phone = c["phone"]
        masked = phone[:7] + "***" + phone[-2:]
        lines.append(f"**{i}.** {c['name']} — {masked}")

    lines.append(f"\n_Total: {len(contacts)} contacts_")
    return "\n".join(lines)


# ══════════════════════════════════════════════
# MESSAGE LOGGING
# ══════════════════════════════════════════════
def _log_message(phone, name, message, channel, success):
    """Log a sent message for tracking."""
    if msg_log_col is None:
        return
    try:
        msg_log_col.insert_one({
            "phone": phone,
            "name": name,
            "message": message[:500],
            "channel": channel,
            "success": success,
            "sent_at": _now(),
        })
    except PyMongoError as e:
        logger.error(f"Message log error: {e}")


def get_reminder_log(guild_id, watchparty_id):
    """Check if reminders were already sent for a specific watch party."""
    if msg_log_col is None:
        return False
    try:
        doc = msg_log_col.find_one({
            "watchparty_id": str(watchparty_id),
            "type": "watchparty_reminder",
        })
        return doc is not None
    except PyMongoError as e:
        logger.error(f"Reminder log check error: {e}")
        return False


def log_reminder_sent(guild_id, watchparty_id):
    """Mark that reminders were sent for a watch party."""
    if msg_log_col is None:
        return
    try:
        msg_log_col.insert_one({
            "guild_id": str(guild_id),
            "watchparty_id": str(watchparty_id),
            "type": "watchparty_reminder",
            "sent_at": _now(),
        })
    except PyMongoError as e:
        logger.error(f"Log reminder error: {e}")

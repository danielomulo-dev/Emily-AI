import os
import logging
import asyncio
import functools
import traceback
from datetime import datetime, timedelta
from collections import defaultdict
import pytz

logger = logging.getLogger(__name__)

EAT_ZONE = pytz.timezone('Africa/Nairobi')

# Bot owner ID — gets DM'd when errors happen
BOT_OWNER_ID = os.getenv("BOT_OWNER_ID")

# ══════════════════════════════════════════════
# ERROR TRACKING
# ══════════════════════════════════════════════
_error_counts = defaultdict(int)  # error_key -> count
_error_last_sent = {}  # error_key -> last time DM was sent
_error_cooldown = timedelta(minutes=10)  # Don't spam DMs for same error


async def notify_owner(bot, error_type, error_msg, context=""):
    """DM the bot owner about an error. Rate-limited to avoid spam."""
    if not BOT_OWNER_ID:
        return

    error_key = f"{error_type}:{str(error_msg)[:100]}"
    _error_counts[error_key] += 1

    # Check cooldown — don't DM for same error within 10 minutes
    now = datetime.now(EAT_ZONE)
    last_sent = _error_last_sent.get(error_key)
    if last_sent and (now - last_sent) < _error_cooldown:
        return

    try:
        owner = bot.get_user(int(BOT_OWNER_ID))
        if not owner:
            owner = await bot.fetch_user(int(BOT_OWNER_ID))

        if owner:
            count = _error_counts[error_key]
            time_str = now.strftime("%I:%M %p EAT")

            dm_text = (
                f"⚠️ **Emily Error Report**\n\n"
                f"**Type:** {error_type}\n"
                f"**Time:** {time_str}\n"
                f"**Occurrences:** {count}\n"
            )
            if context:
                dm_text += f"**Context:** {context}\n"
            dm_text += f"**Error:** `{str(error_msg)[:500]}`"

            await owner.send(dm_text)
            _error_last_sent[error_key] = now
            logger.info(f"Error notification sent to owner: {error_type}")
    except Exception as e:
        logger.error(f"Failed to notify owner: {e}")


# ══════════════════════════════════════════════
# AUTO-RETRY DECORATOR (for sync functions)
# ══════════════════════════════════════════════
def retry(max_retries=3, delay=1, backoff=2, exceptions=(Exception,)):
    """
    Retry decorator for sync functions.
    
    Args:
        max_retries: Maximum number of retries
        delay: Initial delay between retries (seconds)
        backoff: Multiply delay by this after each retry
        exceptions: Tuple of exceptions to catch
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_error = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_error = e
                    if attempt < max_retries:
                        logger.warning(
                            f"Retry {attempt + 1}/{max_retries} for {func.__name__}: {e}"
                        )
                        import time
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            f"All {max_retries} retries failed for {func.__name__}: {e}"
                        )

            raise last_error

        return wrapper
    return decorator


# ══════════════════════════════════════════════
# AUTO-RETRY DECORATOR (for async functions)
# ══════════════════════════════════════════════
def async_retry(max_retries=3, delay=1, backoff=2, exceptions=(Exception,)):
    """Retry decorator for async functions."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            current_delay = delay
            last_error = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_error = e
                    if attempt < max_retries:
                        logger.warning(
                            f"Async retry {attempt + 1}/{max_retries} for {func.__name__}: {e}"
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            f"All {max_retries} async retries failed for {func.__name__}: {e}"
                        )

            raise last_error

        return wrapper
    return decorator


# ══════════════════════════════════════════════
# API CALL WRAPPER WITH RETRY
# ══════════════════════════════════════════════
@retry(max_retries=2, delay=1, backoff=2, exceptions=(Exception,))
def api_call_with_retry(func, *args, **kwargs):
    """Wrap any sync API call with automatic retry."""
    return func(*args, **kwargs)


async def async_api_call_with_retry(coro, max_retries=2, delay=1):
    """Wrap any async API call with automatic retry."""
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return await coro
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                logger.warning(f"Async API retry {attempt + 1}/{max_retries}: {e}")
                await asyncio.sleep(delay * (attempt + 1))
            else:
                logger.error(f"All async API retries failed: {e}")
    raise last_error


# ══════════════════════════════════════════════
# GLOBAL ERROR HANDLER FOR COMMANDS
# ══════════════════════════════════════════════
async def handle_command_error(ctx, error, bot):
    """Central error handler for all bot commands."""
    error_msg = str(error)

    # Known, user-friendly errors
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(f"Missing argument: `{error.param.name}`. Use `!help` for usage.")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.reply("Invalid argument. Check `!help` for the correct format.")
        return
    if isinstance(error, commands.CommandNotFound):
        return  # Silently ignore unknown commands
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.reply(f"Slow down! Try again in {error.retry_after:.0f} seconds.")
        return

    # Unexpected errors — log and notify owner
    logger.error(f"Command error in {ctx.command}: {error}", exc_info=True)

    # DM the owner
    await notify_owner(
        bot,
        error_type=f"Command: !{ctx.command}",
        error_msg=error_msg,
        context=f"User: {ctx.author} | Channel: {ctx.channel}",
    )

    # User-friendly response
    await ctx.reply("Something went wrong. The error has been reported!")


# ══════════════════════════════════════════════
# BACKGROUND TASK ERROR WRAPPER
# ══════════════════════════════════════════════
def task_error_handler(bot):
    """Create an error handler for background tasks."""
    async def handler(task_name, error):
        logger.error(f"Background task error in {task_name}: {error}", exc_info=True)
        await notify_owner(
            bot,
            error_type=f"Task: {task_name}",
            error_msg=str(error),
        )
    return handler

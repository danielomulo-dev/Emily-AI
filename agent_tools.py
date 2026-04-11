"""
agent_tools.py — Add to Emily's codebase on Koyeb.

This module adds agent commands to Emily that call your local
desktop agent server through an ngrok/Cloudflare tunnel.

ENV VARS (add to Koyeb):
  AGENT_URL    = your ngrok/cloudflare tunnel URL (e.g., https://abc123.ngrok-free.app)
  AGENT_SECRET = same secret token as your local agent server

COMMANDS:
  !agent <request>        AI decides what to do on your desktop
  !run <command>           Run a terminal command
  !open <url or app>       Open URL or app on desktop
  !close <app>             Close an app on desktop
  !review <filepath>       Code review with Gemma 4
  !review-claude <filepath> Code review with Claude
  !explain <filepath>      Explain code with Qwen
  !generate <description>  Generate code with Gemma 4
  !read <filepath>         Read a file from desktop
  !ls <directory>          List directory on desktop
  !search <dir> <term>     Search files on desktop
  !ask-qwen <question>     Ask Qwen 2.5 7B
  !ask-gemma <question>    Ask Gemma 4 26B
  !ask-claude <question>   Ask Claude via local agent
  !agent-status            Check if desktop agent is online
  !agent-help              Show agent commands
"""

import os
import aiohttp
import logging
from discord.ext import commands

logger = logging.getLogger("emily")

AGENT_URL = os.getenv("AGENT_URL", "")
AGENT_SECRET = os.getenv("AGENT_SECRET", "")
OWNER_ID = int(os.getenv("DISCORD_OWNER_ID", "0"))


def is_owner(ctx) -> bool:
    return ctx.author.id == OWNER_ID


async def agent_request(endpoint: str, payload: dict) -> dict:
    """Send a request to the local agent server."""
    if not AGENT_URL:
        return {"error": "AGENT_URL not configured. Is your desktop agent running?"}

    url = f"{AGENT_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {AGENT_SECRET}",
        "Content-Type": "application/json"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=300)
            ) as resp:
                if resp.status == 401:
                    return {"error": "Authentication failed. Check AGENT_SECRET."}
                if resp.status != 200:
                    text = await resp.text()
                    return {"error": f"Agent error ({resp.status}): {text[:300]}"}
                return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Can't reach desktop agent. Is it running? Error: {e}"}
    except Exception as e:
        return {"error": f"Unexpected error: {e}"}


async def agent_get(endpoint: str) -> dict:
    """GET request to local agent server."""
    if not AGENT_URL:
        return {"error": "AGENT_URL not configured."}

    url = f"{AGENT_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return await resp.json()
    except Exception as e:
        return {"error": f"Can't reach desktop agent: {e}"}


async def send_chunked(ctx, text: str):
    """Send a message, splitting at 2000 chars for Discord's limit."""
    if len(text) <= 1990:
        await ctx.reply(text)
        return
    chunks = []
    while text:
        if len(text) <= 1990:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, 1990)
        if split_at == -1:
            split_at = text.rfind(" ", 0, 1990)
        if split_at == -1:
            split_at = 1990
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    for i, chunk in enumerate(chunks):
        if i == 0:
            await ctx.reply(chunk)
        else:
            await ctx.send(chunk)


def format_agent_response(data: dict) -> str:
    """Format the agent server response for Discord."""
    if "error" in data:
        return f"**Agent Offline:** {data['error']}"

    if data.get("type") == "agent_response":
        parts = []
        if data.get("thinking"):
            parts.append(f"**Plan:** {data['thinking']}")
        for result in data.get("results", []):
            parts.append(result)
        return "\n\n".join(parts) if parts else "No results."

    if data.get("type") == "direct_response":
        model = data.get("model", "")
        return f"**{model}:** {data.get('result', '')}"

    # Generic format
    if "result" in data:
        model = data.get("model", "")
        header = f"**{model}:**\n" if model else ""
        return f"{header}{data['result']}"

    if "response" in data:
        model = data.get("model", "")
        return f"**{model}:**\n{data['response']}"

    return str(data)


def format_run_response(data: dict) -> str:
    """Format terminal command output."""
    if "error" in data:
        return f"**Agent Offline:** {data['error']}"

    parts = [f"**Command:** `{data.get('command', '?')}`"]
    if data.get("success"):
        parts.append("**Status:** Success")
    else:
        parts.append(f"**Status:** Failed (code {data.get('return_code', '?')})")
    if data.get("stdout"):
        parts.append(f"**Output:**\n```\n{data['stdout'][:1500]}\n```")
    if data.get("stderr"):
        parts.append(f"**Errors:**\n```\n{data['stderr'][:1500]}\n```")
    return "\n".join(parts)


# ─── COMMANDS ────────────────────────────────────────────────

def setup_agent_commands(bot: commands.Bot):
    """Call this from main.py to register all agent commands."""

    @bot.command(name="desk")
    async def agent_cmd(ctx, *, prompt: str):
        if not is_owner(ctx):
            return
        async with ctx.typing():
            data = await agent_request("/agent", {"prompt": prompt})
            await send_chunked(ctx, format_agent_response(data))

    @bot.command(name="run")
    async def run_cmd(ctx, *, command: str):
        if not is_owner(ctx):
            return
        async with ctx.typing():
            data = await agent_request("/run", {"command": command})
            await send_chunked(ctx, format_run_response(data))

    @bot.command(name="open")
    async def open_cmd(ctx, *, target: str):
        if not is_owner(ctx):
            return
        async with ctx.typing():
            data = await agent_request("/open", {"target": target})
            await ctx.reply(data.get("result", data.get("error", "Unknown error")))

    @bot.command(name="close")
    async def close_cmd(ctx, *, app_name: str):
        if not is_owner(ctx):
            return
        async with ctx.typing():
            data = await agent_request("/close", {"app_name": app_name})
            await ctx.reply(data.get("result", data.get("error", "Unknown error")))

    @bot.command(name="desk-review")
    async def review_cmd(ctx, *, filepath: str):
        if not is_owner(ctx):
            return
        async with ctx.typing():
            await ctx.reply(f"Reviewing `{filepath}` with Gemma 4... (may take a moment)")
            data = await agent_request("/review", {"filepath": filepath})
            model = data.get("model", "")
            result = data.get("result", data.get("error", "Unknown error"))
            await send_chunked(ctx, f"**Code Review ({model})**\nFile: `{filepath}`\n\n{result}")

    @bot.command(name="desk-review-claude")
    async def review_claude_cmd(ctx, *, filepath: str):
        if not is_owner(ctx):
            return
        async with ctx.typing():
            await ctx.reply(f"Reviewing `{filepath}` with Claude...")
            data = await agent_request("/review", {"filepath": filepath, "use_claude": True})
            model = data.get("model", "")
            result = data.get("result", data.get("error", "Unknown error"))
            await send_chunked(ctx, f"**Code Review ({model})**\nFile: `{filepath}`\n\n{result}")

    @bot.command(name="desk-explain")
    async def explain_cmd(ctx, *, filepath: str):
        if not is_owner(ctx):
            return
        async with ctx.typing():
            data = await agent_request("/explain", {"filepath": filepath})
            model = data.get("model", "")
            result = data.get("result", data.get("error", "Unknown error"))
            await send_chunked(ctx, f"**Code Explanation ({model})**\nFile: `{filepath}`\n\n{result}")

    @bot.command(name="generate")
    async def generate_cmd(ctx, *, description: str):
        if not is_owner(ctx):
            return
        async with ctx.typing():
            await ctx.reply("Generating code with Gemma 4...")
            data = await agent_request("/generate", {"description": description})
            result = data.get("result", data.get("error", "Unknown error"))
            await send_chunked(ctx, f"**Generated Code (Gemma 4)**\n\n{result}")

    @bot.command(name="read")
    async def read_cmd(ctx, *, filepath: str):
        if not is_owner(ctx):
            return
        async with ctx.typing():
            data = await agent_request("/read", {"filepath": filepath})
            content = data.get("content", data.get("error", "Unknown error"))
            await send_chunked(ctx, f"**{filepath}:**\n```\n{content[:3000]}\n```")

    @bot.command(name="ls")
    async def ls_cmd(ctx, *, dirpath: str = "~"):
        if not is_owner(ctx):
            return
        async with ctx.typing():
            data = await agent_request("/ls", {"dirpath": dirpath})
            await send_chunked(ctx, data.get("result", data.get("error", "Unknown error")))

    @bot.command(name="desk-search")
    async def search_cmd(ctx, dirpath: str, *, term: str):
        if not is_owner(ctx):
            return
        async with ctx.typing():
            data = await agent_request("/search", {"dirpath": dirpath, "term": term})
            await send_chunked(ctx, data.get("result", data.get("error", "Unknown error")))

    @bot.command(name="ask-qwen")
    async def ask_qwen_cmd(ctx, *, question: str):
        if not is_owner(ctx):
            return
        async with ctx.typing():
            data = await agent_request("/ask", {"prompt": question, "model": "qwen"})
            await send_chunked(ctx, format_agent_response(data))

    @bot.command(name="ask-gemma")
    async def ask_gemma_cmd(ctx, *, question: str):
        if not is_owner(ctx):
            return
        async with ctx.typing():
            await ctx.reply("Asking Gemma 4... (model may need to load)")
            data = await agent_request("/ask", {"prompt": question, "model": "gemma"})
            await send_chunked(ctx, format_agent_response(data))

    @bot.command(name="ask-claude")
    async def ask_claude_cmd(ctx, *, question: str):
        if not is_owner(ctx):
            return
        async with ctx.typing():
            data = await agent_request("/ask", {"prompt": question, "model": "claude"})
            await send_chunked(ctx, format_agent_response(data))

    @bot.command(name="agent-status")
    async def agent_status_cmd(ctx):
        if not is_owner(ctx):
            return
        data = await agent_get("/health")
        if "error" in data:
            await ctx.reply(f"**Desktop Agent:** Offline\n{data['error']}")
        else:
            models = data.get("models", {})
            await ctx.reply(
                f"**Desktop Agent:** Online\n"
                f"Fast: `{models.get('fast', '?')}`\n"
                f"Heavy: `{models.get('heavy', '?')}`"
            )

    @bot.command(name="agent-help")
    async def agent_help_cmd(ctx):
        text = (
            "**Emily Desktop Agent Commands:**\n"
            "```\n"
            "!desk <request>         AI decides what to do\n"
            "!run <command>          Run terminal command\n"
            "!open <url/app>         Open URL or app\n"
            "!close <app>            Close an app\n"
            "!desk-review <file>     Code review (Gemma 4)\n"
            "!desk-review-claude <f> Code review (Claude)\n"
            "!desk-explain <file>         Explain code (Qwen)\n"
            "!generate <desc>        Generate code (Gemma 4)\n"
            "!read <file>            Read a file\n"
            "!ls <dir>               List directory\n"
            "!desk-search <dir> <t>  Search in files\n"
            "!ask-qwen <question>    Ask Qwen directly\n"
            "!ask-gemma <question>   Ask Gemma directly\n"
            "!ask-claude <question>  Ask Claude directly\n"
            "!agent-status           Check agent is online\n"
            "!agent-help             This help\n"
            "```"
        )
        await ctx.reply(text)

    logger.info("Agent commands registered.")

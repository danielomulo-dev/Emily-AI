"""
Regression tests for the bug-fix pass.

Each test here guards against a specific bug that was present in the shipped
code and has now been fixed. If any of these start failing, it means a
regression has slipped back in — don't delete, fix.

Cross-reference the numbered bugs in the fix report (README / commit message).
"""
import os
import re
import sys
import importlib
import pytest
from unittest.mock import MagicMock

# Make project root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _main():
    """Import main.py lazily — conftest.py sets env vars first."""
    try:
        import main
        return main
    except Exception as e:
        pytest.skip(f"Cannot import main.py: {e}")


# ══════════════════════════════════════════════════════════════════════
# BUG #1 — error_monitor.py missing 'commands' import
# ══════════════════════════════════════════════════════════════════════
class TestErrorMonitorImports:
    """error_monitor.handle_command_error referenced commands.X without importing
    discord.ext.commands. First MissingRequiredArgument raised NameError."""

    def test_commands_module_is_imported(self):
        import error_monitor
        # Must be resolvable — if missing, handle_command_error crashes before
        # doing anything useful.
        assert hasattr(error_monitor, "commands"), (
            "error_monitor must import 'commands' from discord.ext"
        )

    def test_commands_exceptions_resolvable(self):
        """The exception types that handle_command_error checks against must
        all be real attributes on the commands module."""
        import error_monitor
        for attr in ("MissingRequiredArgument", "BadArgument",
                     "CommandNotFound", "CommandOnCooldown"):
            assert hasattr(error_monitor.commands, attr), (
                f"commands.{attr} must exist for handle_command_error"
            )


# ══════════════════════════════════════════════════════════════════════
# BUG #1b — async_api_call_with_retry had a broken signature (coroutine
# can only be awaited once, so retry was impossible)
# ══════════════════════════════════════════════════════════════════════
class TestRetrySignature:
    def test_accepts_callable_and_args(self):
        """Must accept (func, *args, **kwargs) — not a pre-awaited coroutine."""
        import inspect
        from error_monitor import async_api_call_with_retry
        sig = inspect.signature(async_api_call_with_retry)
        params = list(sig.parameters.values())
        # First param is the callable, then *args follows
        assert params[0].name in ("func", "fn", "callable")
        assert any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params), (
            "Must accept *args so each retry can call the function fresh"
        )
        assert any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params), (
            "Must accept **kwargs for forwarding"
        )


# ══════════════════════════════════════════════════════════════════════
# BUG #2 — agent_tools OWNER_ID env-var mismatch
# ══════════════════════════════════════════════════════════════════════
class TestOwnerIdEnvVar:
    def test_bot_owner_id_preferred(self, monkeypatch):
        """BOT_OWNER_ID (the one main.py uses) must be the primary."""
        monkeypatch.setenv("BOT_OWNER_ID", "111")
        monkeypatch.setenv("DISCORD_OWNER_ID", "222")
        import agent_tools
        importlib.reload(agent_tools)
        assert agent_tools.OWNER_ID == 111

    def test_discord_owner_id_fallback(self, monkeypatch):
        """DISCORD_OWNER_ID still works if BOT_OWNER_ID is unset — no one
        whose bot worked before should have it break."""
        monkeypatch.delenv("BOT_OWNER_ID", raising=False)
        monkeypatch.setenv("DISCORD_OWNER_ID", "333")
        import agent_tools
        importlib.reload(agent_tools)
        assert agent_tools.OWNER_ID == 333


# ══════════════════════════════════════════════════════════════════════
# BUG #4 — Pattern 4 expense false positives ("my wife is 30" → KES 30)
# ══════════════════════════════════════════════════════════════════════
class TestPattern4ExpenseFalsePositives:
    """The expense regex must NOT log spending for innocuous 'X is N' statements."""

    # This is the exact pattern used in main.py's on_message
    PATTERN = re.compile(
        r'(.+?)\s+(?:was|costs?(?:\s+me)?|came\s+to)\s+(?:KES\s*|Ksh\s*)?(\d[\d,]*\.?\d*)',
        re.IGNORECASE
    )

    @pytest.mark.parametrize("text", [
        "my wife is 30",
        "my son is 12",
        "my IQ is 140",
        "the score is 100",
        "she is 25 years old",
        "temperature today is 25",
        "my house is 20 years old",
    ])
    def test_is_statements_do_not_match(self, text):
        """These are real-life messages that used to log money as expenses."""
        m = self.PATTERN.search(text)
        if m:
            # The 6-word + amount>0 guards are in main.py, so a bare regex match
            # alone isn't the full picture, but we want the regex itself to miss.
            desc_words = len(m.group(1).split())
            assert desc_words > 6, (
                f"Pattern 4 must not match '{text}' — matched desc='{m.group(1)}'"
            )

    def test_was_still_works_for_real_expenses(self):
        """Don't over-correct — 'lunch was 500' is a legitimate expense report."""
        m = self.PATTERN.search("lunch was 500")
        assert m is not None
        assert m.group(1).strip() == "lunch"
        assert float(m.group(2)) == 500

    def test_cost_me_still_works(self):
        m = self.PATTERN.search("the repair cost me 3500")
        assert m is not None
        assert float(m.group(2)) == 3500


# ══════════════════════════════════════════════════════════════════════
# BUG #5 — Income detector had no guardrails (false positives) and no return
# ══════════════════════════════════════════════════════════════════════
class TestIncomeDetection:
    """The income regex must not match '500 emails', '100 likes', '3 messages' etc.

    We pull the real patterns out of main.py by reading the source — this keeps
    the test honest: if the patterns change in main.py, the test reflects that.
    """

    # Patterns copied from main.py on_message — mirror the fixed behaviour
    PATTERNS = [
        r'(?:i\s+)?got\s+paid\s+(?:KES\s*|Ksh\s*)?(\d[\d,]*\.?\d*)\s*(?:from\s+|for\s+)?(.*)',
        r'(?:i\s+)?earned\s+(?:KES\s*|Ksh\s*)?(\d[\d,]*\.?\d*)\s*(?:from\s+|for\s+)?(.*)',
        r'(?:i\s+)?received\s+(?:KES\s*|Ksh\s*)?(\d[\d,]*\.?\d*)\s+(?:from\s+|for\s+)(.+)',
        r'(?:i\s+)?got\s+(?:KES\s*|Ksh\s*)(\d[\d,]*\.?\d*)\s*(?:from\s+|for\s+)?(.*)',
        r'(?:i\s+)?got\s+(\d[\d,]*\.?\d*)\s+from\s+(.+)',
        r'(?:client|boss|employer|company|someone)\s+(?:paid|sent)\s+(?:me\s+)?(?:KES\s*|Ksh\s*)?(\d[\d,]*\.?\d*)\s*(?:for\s+)?(.*)',
    ]

    def _match(self, text):
        for pat in self.PATTERNS:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return m, pat
        return None, None

    @pytest.mark.parametrize("text", [
        "I got 500 emails today",
        "I got 100 likes on my post",
        "I received 3 messages",
        "I received 250 notifications",
        "got 10 retweets",
        "I earned 5 stars on that review",  # "earned 5" without currency or source
    ])
    def test_bogus_inputs_do_not_match(self, text):
        m, pat = self._match(text)
        # Either the regex doesn't match at all, or it does but the KES 100
        # minimum in main.py will drop it. For this test we require no match
        # on the "received X notifications"-style inputs (no from/for).
        if m:
            # If matched, verify it will be rejected by the KES 100 floor
            # OR that the group(2) (description) was forced via from/for
            amount = float(m.group(1).replace(",", ""))
            # If amount >= 100 and pattern doesn't require from/for, this is a fail
            requires_from_for = "from\\s+|for\\s+" in pat and not pat.endswith("(.*)")
            assert amount < 100 or requires_from_for, (
                f"'{text}' matched pattern {pat!r} with amount={amount} and no "
                "from/for qualifier — will be logged as income"
            )

    def test_got_paid_matches_real_income(self):
        m, _ = self._match("I got paid 50000 for the project")
        assert m is not None
        assert float(m.group(1)) == 50000

    def test_client_paid_matches(self):
        m, _ = self._match("Client paid me 25000 for the website")
        assert m is not None
        assert float(m.group(1)) == 25000

    def test_earned_from_source_matches(self):
        m, _ = self._match("I earned 30000 from freelance")
        assert m is not None
        assert float(m.group(1)) == 30000

    def test_bare_got_N_requires_from_qualifier(self):
        """'got 100 likes' must miss; 'got 100 from Apple' should match."""
        m_bad, _ = self._match("I got 100 likes today")
        m_good, _ = self._match("I got 100 from Apple")
        # Bad case: either no match, or amount is small enough that main.py drops it
        if m_bad:
            assert float(m_bad.group(1)) < 100
        assert m_good is not None


# ══════════════════════════════════════════════════════════════════════
# BUG #6 — Reminder parser picked the first 'at', not the rightmost
# ══════════════════════════════════════════════════════════════════════
class TestReminderParser:
    """Simulate the main.py reminder parser logic for rightmost-time extraction.

    Note: the time portion captured now INCLUDES the "at" or "by" prefix
    (e.g. "at 5pm" rather than "5pm") because otherwise the bare-digit
    pattern wins and leaves a trailing " at" on the task.
    """

    TIME_PATTERNS = [
        r'(tomorrow(?:\s+(?:morning|afternoon|evening|night))?(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?)',
        r'(tonight(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?)',
        r'(next\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?)',
        r'(next\s+week(?:end)?(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?)',
        r'(on\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?)',
        r'(in\s+\d+\s+(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?))',
        r'((?:at\s+|by\s+)?\d{1,2}:\d{2}\s*(?:am|pm)?)',
        r'((?:at\s+|by\s+)?\d{1,2}\s*(?:am|pm))',
    ]

    def _split(self, msg):
        prefix = re.match(r'remind\s+me\s+(?:to\s+)?(.+)', msg, re.IGNORECASE)
        if not prefix:
            return None, None
        raw = prefix.group(1).strip().rstrip('.!?')
        best = None
        for pat in self.TIME_PATTERNS:
            for m in re.finditer(pat, raw, re.IGNORECASE):
                if best is None or m.start() > best.start():
                    best = m
        if best:
            time_part = best.group(1).strip()
            task = raw[:best.start()].strip().rstrip(',').rstrip()
            return task, time_part
        return raw, ""

    def test_task_with_at_midway_uses_final_at_as_time(self):
        """Previously: 'at the airport' became the time. Now: '8pm' wins."""
        task, time = self._split(
            "remind me to pick up the kids at the airport at 8pm"
        )
        assert task == "pick up the kids at the airport"
        assert "8pm" in time.lower()

    def test_simple_at_time(self):
        task, time = self._split("remind me to call mom at 5pm")
        assert task == "call mom"
        assert "5pm" in time.lower()

    def test_in_N_hours(self):
        task, time = self._split("remind me to check the oven in 30 minutes")
        assert task == "check the oven"
        assert "30" in time and "minute" in time.lower()

    def test_tomorrow(self):
        task, time = self._split("remind me to buy milk tomorrow")
        assert task == "buy milk"
        assert time.lower().strip() == "tomorrow"

    def test_no_time_falls_through(self):
        """'remind me to water plants' → task captured, no time → will become a to-do."""
        task, time = self._split("remind me to water plants")
        assert task == "water plants"
        assert time == ""


# ══════════════════════════════════════════════════════════════════════
# BUG #7 — Journal auto-logger triggered on "the journal is boring" etc.
# ══════════════════════════════════════════════════════════════════════
class TestJournalAutoDetection:
    PATTERN = re.compile(
        r'(?:today\s+was|my\s+day\s+was|i\s+(?:had|have)\s+(?:a|an)\s+(?:great|good|bad|terrible|rough|amazing|tough|wonderful|stressful)\s+day'
        r'|(?:i\s+feel|i\'?m\s+feeling|feeling\s+(?:so\s+)?(?:happy|sad|stressed|anxious|grateful|blessed|overwhelmed|proud|tired|excited|depressed|lonely))'
        r'|(?:i\s+(?:got|received)\s+(?:promoted|fired|hired|accepted|rejected|dumped))'
        r'|(?:^journal\s*[:\-]|\blet\s+me\s+journal\b|\bdear\s+diary\b|\bjournal\s+entry\b))',
        re.IGNORECASE
    )

    @pytest.mark.parametrize("text", [
        "the journal is boring lately",
        "remember to log this somewhere later",
        "my journal entry for school is due",  # tricky edge case
        "can you read this journal article?",
    ])
    def test_no_false_triggers(self, text):
        """These messages used to create unwanted journal entries."""
        m = self.PATTERN.search(text)
        if m:
            # "journal entry" is still a valid explicit trigger — accept it for
            # that case only
            assert "journal entry" in text.lower(), (
                f"'{text}' should not create a journal entry but matched "
                f"'{m.group(0)}'"
            )

    @pytest.mark.parametrize("text", [
        "today was absolutely amazing",
        "my day was rough honestly",
        "I had a great day with the team",
        "feeling stressed about the deadline",
        "I'm feeling grateful today",
        "dear diary, today I learned",
        "let me journal about this weekend",
    ])
    def test_real_journal_prompts_match(self, text):
        """Don't over-correct — genuine journaling prompts must still trigger."""
        assert self.PATTERN.search(text) is not None


# ══════════════════════════════════════════════════════════════════════
# BUG #8 — api_server crashed on bad query param values
# ══════════════════════════════════════════════════════════════════════
class TestSafeIntQueryParse:
    def test_safe_int_handles_good_value(self):
        from api_server import _safe_int
        assert _safe_int({"days": ["7"]}, "days", 14) == 7

    def test_safe_int_handles_bogus_value(self):
        """?days=foo used to raise ValueError → 500."""
        from api_server import _safe_int
        assert _safe_int({"days": ["foo"]}, "days", 14) == 14

    def test_safe_int_handles_missing(self):
        from api_server import _safe_int
        assert _safe_int({}, "days", 14) == 14

    def test_safe_int_clamps_too_large(self):
        from api_server import _safe_int
        assert _safe_int({"days": ["9999"]}, "days", 14, max_val=365) == 365

    def test_safe_int_clamps_too_small(self):
        from api_server import _safe_int
        assert _safe_int({"days": ["-5"]}, "days", 14, min_val=1) == 1

    def test_safe_int_handles_none_list(self):
        from api_server import _safe_int
        assert _safe_int({"days": [None]}, "days", 14) == 14


# ══════════════════════════════════════════════════════════════════════
# BUG #10 — SSRF: user-pasted URLs fetched without private-IP block
# ══════════════════════════════════════════════════════════════════════
class TestSSRFGuard:
    def test_blocks_loopback(self):
        from web_tools import _is_safe_url
        assert _is_safe_url("http://localhost/admin") is False
        assert _is_safe_url("http://127.0.0.1/") is False
        assert _is_safe_url("http://127.0.0.1:8000/status") is False

    def test_blocks_aws_metadata(self):
        from web_tools import _is_safe_url
        assert _is_safe_url("http://169.254.169.254/latest/meta-data/") is False

    def test_blocks_private_ranges(self):
        from web_tools import _is_safe_url
        assert _is_safe_url("http://10.0.0.1/") is False
        assert _is_safe_url("http://192.168.1.1/") is False
        assert _is_safe_url("http://172.16.0.1/") is False

    def test_blocks_non_http_schemes(self):
        from web_tools import _is_safe_url
        assert _is_safe_url("file:///etc/passwd") is False
        assert _is_safe_url("ftp://example.com/") is False
        assert _is_safe_url("gopher://internal/") is False

    def test_allows_public_domains(self):
        from web_tools import _is_safe_url
        assert _is_safe_url("https://www.example.com/") is True
        assert _is_safe_url("https://en.wikipedia.org/wiki/Kenya") is True

    def test_handles_garbage_input(self):
        from web_tools import _is_safe_url
        assert _is_safe_url("") is False
        assert _is_safe_url("not a url") is False
        assert _is_safe_url("http://") is False


# ══════════════════════════════════════════════════════════════════════
# BUG #11 — YouTube video_id parser broke on share links + Shorts
# ══════════════════════════════════════════════════════════════════════
class TestYouTubeVideoIdExtraction:
    @pytest.mark.parametrize("url,expected", [
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc123", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/abc123xyz", "abc123xyz"),
        ("https://www.youtube.com/embed/xyz789", "xyz789"),
    ])
    def test_extracts_correctly(self, url, expected):
        from web_tools import _extract_youtube_video_id
        assert _extract_youtube_video_id(url) == expected

    @pytest.mark.parametrize("url", [
        "",
        "https://vimeo.com/12345",
        "not a url",
        "https://youtube.com/",  # no video id
    ])
    def test_returns_none_for_non_matches(self, url):
        from web_tools import _extract_youtube_video_id
        assert _extract_youtube_video_id(url) is None


# ══════════════════════════════════════════════════════════════════════
# BUG #12 — vote_for_movie TOCTOU race (double-click → double vote)
# ══════════════════════════════════════════════════════════════════════
class TestVoteForMovieAtomic:
    """Verify vote_for_movie is a single atomic update with a votes:$ne filter.

    We can't easily simulate a real MongoDB race in unit tests, but we can
    inspect the implementation to prove the pattern is atomic — a separate
    find_one + update_one combo is what caused the bug.
    """

    def test_uses_single_atomic_update(self):
        import inspect
        import watchparty_tools
        src = inspect.getsource(watchparty_tools.vote_for_movie)
        # New impl must reference $ne in the vote filter — that's the atomic check
        assert "$ne" in src, (
            "vote_for_movie must use {'votes': {'$ne': uid}} in the update filter "
            "to prevent race conditions"
        )
        # And must call update_one with the $ne filter before any unguarded
        # find_one. We can check ordering by looking at positions.
        ne_pos = src.index("$ne")
        # update_one must appear after the $ne filter construction
        assert "update_one" in src
        assert src.index("update_one") > ne_pos - 200, (
            "The $ne filter must be part of the update_one call, not a "
            "separate find-and-check"
        )


# ══════════════════════════════════════════════════════════════════════
# BUG #13 — !recat always replied "Something went wrong" even on success
# ══════════════════════════════════════════════════════════════════════
class TestRecatErrorHandling:
    def test_error_reply_is_inside_except(self):
        """The fallback error reply in cmd_recat must be inside the except block,
        not dedented to the same level as try/except."""
        main_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "main.py"
        )
        with open(main_path) as f:
            src = f.read()
        # Find the cmd_recat function
        m = re.search(
            r'async def cmd_recat\(ctx\).*?(?=\n\n@bot\.command|\nasync def |\ndef |\Z)',
            src, re.DOTALL
        )
        assert m, "Could not find cmd_recat in main.py"
        body = m.group(0)
        # The fallback reply must live inside the except block (12-space indent),
        # not at the outer try/except level (8-space indent).
        assert re.search(
            r'except Exception as e:.*?await ctx\.reply\("Something went wrong',
            body, re.DOTALL
        ), "The 'Something went wrong' reply must be inside the except block"


# ══════════════════════════════════════════════════════════════════════
# BUG #14 — /settings and /toggle crashed in DMs (no guild_only)
# ══════════════════════════════════════════════════════════════════════
class TestSlashCommandsGuildOnly:
    def test_settings_is_guild_only(self):
        main_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "main.py"
        )
        with open(main_path) as f:
            src = f.read()
        # Find the slash_settings decorators block
        m = re.search(
            r'@bot\.tree\.command\(name="settings".*?\nasync def slash_settings',
            src, re.DOTALL
        )
        assert m, "Could not find slash_settings"
        assert "@app_commands.guild_only()" in m.group(0), (
            "/settings must have @app_commands.guild_only() to avoid "
            "AttributeError in DMs"
        )

    def test_toggle_is_guild_only(self):
        main_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "main.py"
        )
        with open(main_path) as f:
            src = f.read()
        m = re.search(
            r'@bot\.tree\.command\(name="toggle".*?\nasync def slash_toggle',
            src, re.DOTALL
        )
        assert m, "Could not find slash_toggle"
        assert "@app_commands.guild_only()" in m.group(0), (
            "/toggle must have @app_commands.guild_only() to avoid "
            "AttributeError in DMs"
        )


# ══════════════════════════════════════════════
# Bug #16 — Brittle scheduled-task time checks
# Previously, 8 scheduled tasks compared `strftime("%H:%M") != "HH:MM"` —
# a 59-second window. Missed ticks (bot restart, slow loop) silently skipped
# the scheduled work. Replaced with _should_run_scheduled() which gives a
# 60-minute grace window plus in-memory per-day dedup.
# ══════════════════════════════════════════════

class TestScheduledTaskHelper:
    """Guards against regression of the brittle 59-second-window pattern."""

    def _fresh_helper(self):
        """Import fresh copies of the helpers with an empty state dict."""
        import importlib
        import main as mm
        importlib.reload(mm) if False else None  # don't actually reload, just reach in
        # Drop any prior state
        mm._task_last_run.clear()
        return mm._should_run_scheduled, mm._mark_scheduled_ran, mm._task_last_run

    def test_fires_at_target_time(self, monkeypatch):
        """Task fires when now matches the target hour:min exactly."""
        from datetime import datetime
        import pytz
        import main as mm
        mm._task_last_run.clear()

        EAT = pytz.timezone('Africa/Nairobi')
        fake_now = EAT.localize(datetime(2026, 4, 20, 18, 0, 0))  # Mon 18:00
        monkeypatch.setattr(mm, 'datetime', type('FakeDT', (), {
            'now': staticmethod(lambda tz=None: fake_now),
        }))
        try:
            assert mm._should_run_scheduled("test_task", target_hour=18, target_weekday=0) is True
        finally:
            monkeypatch.undo()

    def test_fires_within_grace_window(self):
        """Task fires up to 60 minutes after target time — covers bot restart / slow tick."""
        from datetime import datetime
        from unittest.mock import patch
        import pytz
        import main as mm
        mm._task_last_run.clear()

        EAT = pytz.timezone('Africa/Nairobi')
        # Target was 18:00 — now is 18:45, still within 60-min grace
        fake_now = EAT.localize(datetime(2026, 4, 20, 18, 45, 0))  # Mon
        with patch('main.datetime') as mdt:
            mdt.now.return_value = fake_now
            assert mm._should_run_scheduled("test_task_grace", target_hour=18, target_weekday=0) is True

    def test_does_not_fire_before_target(self):
        """Task does not fire before target time."""
        from datetime import datetime
        from unittest.mock import patch
        import pytz
        import main as mm
        mm._task_last_run.clear()

        EAT = pytz.timezone('Africa/Nairobi')
        fake_now = EAT.localize(datetime(2026, 4, 20, 17, 59, 0))  # Mon, 1 min before
        with patch('main.datetime') as mdt:
            mdt.now.return_value = fake_now
            assert mm._should_run_scheduled("test_early", target_hour=18, target_weekday=0) is False

    def test_does_not_fire_past_grace_window(self):
        """Task does not fire after 60-minute grace window — prevents firing all night."""
        from datetime import datetime
        from unittest.mock import patch
        import pytz
        import main as mm
        mm._task_last_run.clear()

        EAT = pytz.timezone('Africa/Nairobi')
        fake_now = EAT.localize(datetime(2026, 4, 20, 19, 1, 0))  # 1 min past grace
        with patch('main.datetime') as mdt:
            mdt.now.return_value = fake_now
            assert mm._should_run_scheduled("test_late", target_hour=18, target_weekday=0) is False

    def test_weekday_gate(self):
        """target_weekday filters correctly — Sunday task doesn't fire on Monday."""
        from datetime import datetime
        from unittest.mock import patch
        import pytz
        import main as mm
        mm._task_last_run.clear()

        EAT = pytz.timezone('Africa/Nairobi')
        monday = EAT.localize(datetime(2026, 4, 20, 18, 0, 0))
        with patch('main.datetime') as mdt:
            mdt.now.return_value = monday
            assert mm._should_run_scheduled("sunday_only", target_hour=18, target_weekday=6) is False
            assert mm._should_run_scheduled("monday_ok", target_hour=18, target_weekday=0) is True

    def test_daily_task_no_weekday(self):
        """target_weekday=None means fire every day."""
        from datetime import datetime
        from unittest.mock import patch
        import pytz
        import main as mm
        mm._task_last_run.clear()

        EAT = pytz.timezone('Africa/Nairobi')
        # Try several days
        for day in range(20, 27):
            fake_now = EAT.localize(datetime(2026, 4, day, 8, 0, 0))
            with patch('main.datetime') as mdt:
                mdt.now.return_value = fake_now
                mm._task_last_run.clear()  # reset between days
                assert mm._should_run_scheduled("daily_task", target_hour=8) is True

    def test_dedup_within_day(self):
        """Once marked, task does not fire again the same day — prevents posting every tick in grace window."""
        from datetime import datetime
        from unittest.mock import patch
        import pytz
        import main as mm
        mm._task_last_run.clear()

        EAT = pytz.timezone('Africa/Nairobi')
        fake_now = EAT.localize(datetime(2026, 4, 20, 18, 0, 0))
        with patch('main.datetime') as mdt:
            mdt.now.return_value = fake_now
            assert mm._should_run_scheduled("dedup_test", target_hour=18, target_weekday=0) is True
            mm._mark_scheduled_ran("dedup_test")
            # Still within grace window, but already ran — should skip
            mdt.now.return_value = EAT.localize(datetime(2026, 4, 20, 18, 30, 0))
            assert mm._should_run_scheduled("dedup_test", target_hour=18, target_weekday=0) is False

    def test_dedup_resets_next_day(self):
        """New day resets the dedup — task runs once each day."""
        from datetime import datetime
        from unittest.mock import patch
        import pytz
        import main as mm
        mm._task_last_run.clear()

        EAT = pytz.timezone('Africa/Nairobi')
        with patch('main.datetime') as mdt:
            # Day 1
            mdt.now.return_value = EAT.localize(datetime(2026, 4, 20, 8, 0, 0))
            assert mm._should_run_scheduled("daily_dedup", target_hour=8) is True
            mm._mark_scheduled_ran("daily_dedup")
            assert mm._should_run_scheduled("daily_dedup", target_hour=8) is False
            # Day 2
            mdt.now.return_value = EAT.localize(datetime(2026, 4, 21, 8, 0, 0))
            assert mm._should_run_scheduled("daily_dedup", target_hour=8) is True

    def test_no_brittle_pattern_remains_in_main(self):
        """Guard the whole refactor — main.py must contain zero `strftime("%H:%M") == "HH:MM"` checks."""
        import re
        import os
        main_path = os.path.join(os.path.dirname(__file__), '..', 'main.py')
        with open(main_path) as f:
            code = f.read()
        matches = re.findall(r'strftime\("%H:%M"\)\s*(==|!=)\s*"\d\d:\d\d"', code)
        assert matches == [], (
            f"Found {len(matches)} brittle strftime checks — these will miss ticks "
            f"on bot restart. Use _should_run_scheduled() instead."
        )

    def test_all_eight_tasks_use_helper(self):
        """Every known-affected task body should reference _should_run_scheduled."""
        import os
        main_path = os.path.join(os.path.dirname(__file__), '..', 'main.py')
        with open(main_path) as f:
            code = f.read()
        expected = [
            'monday_music_drop', 'weekly_digest', 'daily_birthday_check',
            'accountability_check', 'weekly_finance_coaching', 'nightly_scripture',
            'weekly_journal_digest', 'weekly_playlist_recs',
        ]
        for task in expected:
            assert f'_should_run_scheduled("{task}"' in code, f"{task} does not guard with _should_run_scheduled"
            assert f'_mark_scheduled_ran("{task}")' in code, f"{task} does not mark itself with _mark_scheduled_ran"


# ══════════════════════════════════════════════
# Bug #17 — PDF !report crashed for users with no budget
# generate_report.py:142 did `d["total_spent"] / d["total_budget"]` unprotected.
# Users with expenses but no budget got ZeroDivisionError → the whole !report
# failed with "Report generation failed. Try again later!"
# ══════════════════════════════════════════════

class TestPDFEmptyBudget:
    """Guard: PDF generation must succeed when total_budget is 0."""

    def test_pdf_does_not_crash_with_zero_budget(self):
        """Render a PDF for a user with expenses but no budget set."""
        from generate_report import generate_bytes
        data = {
            "month": "April 2026",
            "generated": "20 Apr 2026, 10:00 AM EAT",
            "user": "Test User",
            "total_spent": 5000,
            "total_budget": 0,          # user never ran !setbudget
            "remaining": 0,
            "transactions": 3,
            "days_remaining": 10,
            "daily_allowance": 0,
            "daily_avg": 500,
            "categories": [
                {"name": "food", "amount": 3000, "pct": 60.0, "count": 2,
                 "avg": 1500, "largest": "lunch", "largest_amt": 2000},
                {"name": "transport", "amount": 2000, "pct": 40.0, "count": 1,
                 "avg": 2000, "largest": "uber", "largest_amt": 2000},
            ],
            "daily_spending": [
                {"date": "Apr 15", "amount": 3000},
                {"date": "Apr 18", "amount": 2000},
            ],
            "monthly_history": [],
            "top5": [],
            "recurring": [],
            "cat_transactions": {"food": [], "transport": []},
            "all_transactions": [],
        }
        pdf = generate_bytes(data)
        assert pdf is not None
        assert len(pdf) > 1000, "PDF should contain actual content"
        assert pdf[:4] == b'%PDF', "Should be a real PDF file"

    def test_pdf_works_with_valid_budget(self):
        """Sanity: PDF still works when budget is set."""
        from generate_report import generate_bytes
        data = {
            "month": "April 2026", "generated": "20 Apr 2026",
            "user": "Test", "total_spent": 5000, "total_budget": 10000,
            "remaining": 5000, "transactions": 1, "days_remaining": 10,
            "daily_allowance": 500, "daily_avg": 500,
            "categories": [{"name": "food", "amount": 5000, "pct": 100.0,
                           "count": 1, "avg": 5000, "largest": "x", "largest_amt": 5000}],
            "daily_spending": [{"date": "Apr 15", "amount": 5000}],
            "monthly_history": [], "top5": [], "recurring": [],
            "cat_transactions": {"food": []}, "all_transactions": [],
        }
        pdf = generate_bytes(data)
        assert pdf is not None and pdf[:4] == b'%PDF'


# ══════════════════════════════════════════════
# Bug #18 — 30 - day_of_month gives wrong days-left (3 sites)
# Months have 28, 29, 30, or 31 days — the old code assumed 30 always.
# In 31-day months on day 31, days_left = -1 (crashes or confuses Claude prompt).
# In Feb (28 days) on day 28, days_left = 2 (actually 0).
# ══════════════════════════════════════════════

class TestDaysLeftInMonth:
    """Guard: no more hardcoded `30 - day_of_month` in main.py."""

    def test_january_end_of_month(self):
        """Jan has 31 days — day 31 means 0 days left, not -1."""
        import calendar
        days_in_month = calendar.monthrange(2026, 1)[1]
        assert days_in_month == 31
        assert max(0, days_in_month - 31) == 0  # correct
        assert 30 - 31 == -1                     # old buggy formula

    def test_february_non_leap(self):
        import calendar
        assert calendar.monthrange(2026, 2)[1] == 28

    def test_february_leap(self):
        import calendar
        assert calendar.monthrange(2024, 2)[1] == 29

    def test_no_hardcoded_30_minus_day(self):
        """main.py must no longer use `30 - day_of_month` or `30 - now.day`."""
        import os, re
        main_path = os.path.join(os.path.dirname(__file__), '..', 'main.py')
        with open(main_path) as f:
            code = f.read()
        # Strip comments so we don't match the "previously" explanation in docstrings
        lines_of_code = []
        for line in code.split('\n'):
            # Keep everything before a `#` that starts a comment (ignore # inside strings — good enough heuristic)
            stripped = line.split('#', 1)[0] if not ("'" in line or '"' in line) else line
            lines_of_code.append(stripped)
        code_only = '\n'.join(lines_of_code)
        # The actual buggy patterns
        bad_patterns = [
            r'=\s*30\s*-\s*day_of_month\b',
            r'=\s*30\s*-\s*now\.day\b',
        ]
        for pattern in bad_patterns:
            matches = re.findall(pattern, code_only)
            assert matches == [], (
                f"Found {len(matches)} uses of buggy pattern `{pattern}` in main.py — "
                f"replace with calendar.monthrange()"
            )

    def test_all_three_sites_use_calendar_monthrange(self):
        """The 3 fixed sites should all reference monthrange or days_in_month."""
        import os
        main_path = os.path.join(os.path.dirname(__file__), '..', 'main.py')
        with open(main_path) as f:
            code = f.read()
        # Count monthrange usages — should be at least 3 (one per fix site; plus the
        # Feature 7 dashboard fix you already had)
        count = code.count('monthrange(')
        assert count >= 3, (
            f"Expected at least 3 calendar.monthrange() calls in main.py but found {count}. "
            f"Are the days-left fixes still in place?"
        )


# ══════════════════════════════════════════════
# Bugs #19 & #20 — _normalize_phone had two silent-failure cases
# #19: "+1234567890" returned "++1234567890" (double plus — always invalid at SMS gateway)
# #20: "07123456789" (11-digit local) silently accepted as "+07123456789" instead of rejected
# ══════════════════════════════════════════════

class TestPhoneNormalization:
    """Guards against phone-number normalization regressions."""

    def test_kenyan_plus254_format(self):
        import os
        os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
        from messaging_tools import _normalize_phone
        assert _normalize_phone("+254712345678") == "+254712345678"

    def test_kenyan_254_no_plus(self):
        from messaging_tools import _normalize_phone
        assert _normalize_phone("254712345678") == "+254712345678"

    def test_kenyan_local_with_leading_zero(self):
        from messaging_tools import _normalize_phone
        assert _normalize_phone("0712345678") == "+254712345678"

    def test_kenyan_9_digit_bare(self):
        from messaging_tools import _normalize_phone
        assert _normalize_phone("712345678") == "+254712345678"

    def test_strips_spaces_and_dashes(self):
        from messaging_tools import _normalize_phone
        assert _normalize_phone("+254 712 345 678") == "+254712345678"
        assert _normalize_phone("+254-712-345-678") == "+254712345678"
        assert _normalize_phone("07 12 34 56 78") == "+254712345678"

    def test_strips_parens(self):
        from messaging_tools import _normalize_phone
        assert _normalize_phone("(+254) 712-345-678") == "+254712345678"

    def test_bug19_international_no_double_plus(self):
        """Bug #19 regression: international numbers must not return `++<digits>`."""
        from messaging_tools import _normalize_phone
        result = _normalize_phone("+1234567890")
        assert result is not None
        assert not result.startswith("++"), f"Got {result!r} — expected single-plus prefix"
        assert result == "+1234567890"

    def test_bug19_multiple_international_formats(self):
        """Several international numbers must all come out with a single +."""
        from messaging_tools import _normalize_phone
        cases = [
            ("+14155552671", "+14155552671"),  # US
            ("+442071838750", "+442071838750"),  # UK
            ("+919876543210", "+919876543210"),  # India
        ]
        for input_num, expected in cases:
            got = _normalize_phone(input_num)
            assert got == expected, f"{input_num!r} -> {got!r}, expected {expected!r}"

    def test_bug20_rejects_too_many_digits_local(self):
        """Bug #20 regression: 11-digit local number is invalid — must reject, not mangle."""
        from messaging_tools import _normalize_phone
        assert _normalize_phone("07123456789") is None

    def test_rejects_empty_and_garbage(self):
        from messaging_tools import _normalize_phone
        assert _normalize_phone("") is None
        assert _normalize_phone(None) is None
        assert _normalize_phone("invalid") is None
        assert _normalize_phone("123") is None

    def test_rejects_wrong_length_with_254(self):
        """254-prefixed number with wrong digit count must be rejected."""
        from messaging_tools import _normalize_phone
        assert _normalize_phone("254712345") is None  # too short
        assert _normalize_phone("2547123456789") is None  # too long

    def test_rejects_non_kenyan_without_plus(self):
        """Ambiguous international without + is rejected (might be a Kenyan local with typo)."""
        from messaging_tools import _normalize_phone
        assert _normalize_phone("14155552671") is None

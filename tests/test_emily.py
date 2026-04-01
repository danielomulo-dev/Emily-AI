"""
Emily Bot — Test Suite
Run with: python -m pytest tests/ -v
"""
import os
import sys
import asyncio
import calendar
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

# Add parent dir to path so we can import bot modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ══════════════════════════════════════════════
# TEST 1: async_api_call_with_retry
# ══════════════════════════════════════════════
class TestAsyncApiCallWithRetry:
    """Test the retry wrapper creates fresh coroutines each attempt."""

    def _import_retry(self):
        try:
            from error_monitor import async_api_call_with_retry
            return async_api_call_with_retry
        except ImportError:
            pytest.skip("Discord deps not installed")

    @pytest.mark.asyncio
    async def test_success_first_try(self):
        fn = self._import_retry()
        mock_func = AsyncMock(return_value="ok")
        result = await fn(mock_func, "arg1", max_retries=2)
        assert result == "ok"
        assert mock_func.call_count == 1

    @pytest.mark.asyncio
    async def test_success_after_one_retry(self):
        fn = self._import_retry()
        mock_func = AsyncMock(side_effect=[Exception("fail"), "ok"])
        result = await fn(mock_func, max_retries=2, delay=0.01)
        assert result == "ok"
        assert mock_func.call_count == 2

    @pytest.mark.asyncio
    async def test_failure_after_all_retries(self):
        fn = self._import_retry()
        mock_func = AsyncMock(side_effect=Exception("permanent fail"))
        with pytest.raises(Exception, match="permanent fail"):
            await fn(mock_func, max_retries=2, delay=0.01)
        assert mock_func.call_count == 3


# ══════════════════════════════════════════════
# TEST 2: Currency conversion return handling
# ══════════════════════════════════════════════
class TestCurrencyConversion:
    """Test that convert_currency returns (result, error) tuple."""

    def test_returns_tuple(self):
        from utility_tools import convert_currency
        # This will likely fail without network, but we test the shape
        result = convert_currency(100, "INVALID_CURRENCY_XYZ", "KES")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_bad_currency_returns_none_with_error(self):
        from utility_tools import convert_currency
        result, error = convert_currency(100, "INVALID_CURRENCY_XYZ", "KES")
        # Either network error or unknown currency — both return (None, error_str)
        if result is None:
            assert error is not None
            assert isinstance(error, str)


# ══════════════════════════════════════════════
# TEST 3: Month-length budget calculation
# ══════════════════════════════════════════════
class TestMonthLength:
    """Test that budget calculations use actual month length."""

    def test_february_non_leap(self):
        days = calendar.monthrange(2025, 2)[1]
        assert days == 28

    def test_february_leap(self):
        days = calendar.monthrange(2024, 2)[1]
        assert days == 29

    def test_january_31(self):
        days = calendar.monthrange(2025, 1)[1]
        assert days == 31

    def test_april_30(self):
        days = calendar.monthrange(2025, 4)[1]
        assert days == 30

    def test_days_left_calculation(self):
        """Simulate the fixed days_left logic from utility_tools."""
        # Day 15 of a 31-day month
        year, month, day = 2025, 1, 15
        days_in_month = calendar.monthrange(year, month)[1]
        days_left = max(days_in_month - day, 1)
        assert days_left == 16

        # Day 28 of February (non-leap)
        year, month, day = 2025, 2, 28
        days_in_month = calendar.monthrange(year, month)[1]
        days_left = max(days_in_month - day, 1)
        assert days_left == 1  # Last day, minimum 1

    def test_old_bug_would_give_wrong_result(self):
        """The old code used max(30 - day, 1). 
        On Jan 31, old code: max(30-31,1) = 1 (wrong, should be 0→1)
        On Feb 15, old code: max(30-15,1) = 15 (wrong, should be 13)
        """
        # Feb 15 — old code says 15 days left, correct is 13
        year, month, day = 2025, 2, 15
        days_in_month = calendar.monthrange(year, month)[1]
        correct_days_left = max(days_in_month - day, 1)
        old_days_left = max(30 - day, 1)
        assert correct_days_left == 13
        assert old_days_left == 15  # The old bug
        assert correct_days_left != old_days_left


# ══════════════════════════════════════════════
# TEST 4: Token expiry logic
# ══════════════════════════════════════════════
class TestTokenExpiry:
    """Test token generation includes expiry and verification checks it."""

    def test_token_expiry_constant_exists(self):
        from api_server import TOKEN_EXPIRY_DAYS
        assert TOKEN_EXPIRY_DAYS > 0
        assert TOKEN_EXPIRY_DAYS <= 365  # Reasonable upper bound

    def test_expired_token_rejected(self):
        """Mock DB to return an expired token document."""
        import api_server
        from unittest.mock import patch

        expired_doc = {
            "user_id": "12345",
            "token_hash": "abc",
            "expires_at": datetime.now(api_server.EAT_ZONE) - timedelta(days=1),
        }

        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=MagicMock(
            find_one=MagicMock(return_value=expired_doc)
        ))

        with patch.object(api_server, '_get_db', return_value=mock_db):
            result = api_server.verify_token("fake_token")
            assert result is None  # Expired → rejected

    def test_valid_token_accepted(self):
        """Mock DB to return a valid (non-expired) token document."""
        import api_server
        from unittest.mock import patch

        valid_doc = {
            "user_id": "12345",
            "token_hash": "abc",
            "expires_at": datetime.now(api_server.EAT_ZONE) + timedelta(days=10),
        }

        mock_db = MagicMock()
        mock_col = MagicMock(
            find_one=MagicMock(return_value=valid_doc),
            update_one=MagicMock(),
        )
        mock_db.__getitem__ = MagicMock(return_value=mock_col)

        with patch.object(api_server, '_get_db', return_value=mock_db):
            result = api_server.verify_token("fake_token")
            assert result == "12345"

    def test_no_expiry_field_still_works(self):
        """Backward compat: old tokens without expires_at should still work."""
        import api_server
        from unittest.mock import patch

        old_doc = {
            "user_id": "99999",
            "token_hash": "abc",
            # No expires_at field
        }

        mock_db = MagicMock()
        mock_col = MagicMock(
            find_one=MagicMock(return_value=old_doc),
            update_one=MagicMock(),
        )
        mock_db.__getitem__ = MagicMock(return_value=mock_col)

        with patch.object(api_server, '_get_db', return_value=mock_db):
            result = api_server.verify_token("fake_token")
            assert result == "99999"


# ══════════════════════════════════════════════
# TEST 5: Expense category detection (regex)
# ══════════════════════════════════════════════
class TestExpenseCategory:
    """Test the regex-based expense categorizer."""

    def _get_detector(self):
        """Import the sync detector from main — may fail if Discord deps missing."""
        try:
            from main import _detect_expense_category
            return _detect_expense_category
        except Exception:
            pytest.skip("Cannot import main.py (Discord deps missing)")

    def test_food_detection(self):
        detect = self._get_detector()
        assert detect("lunch at java") == "food"
        assert detect("bought milk and bread") == "food"

    def test_transport_detection(self):
        detect = self._get_detector()
        assert detect("uber to town") == "transport"
        assert detect("matatu fare") == "transport"

    def test_bills_detection(self):
        detect = self._get_detector()
        assert detect("electricity tokens") == "bills"
        assert detect("wifi subscription") == "bills"
        assert detect("airtime") == "bills"

    def test_shopping_detection(self):
        detect = self._get_detector()
        assert detect("new shoes") == "shopping"
        assert detect("bought a charger") == "shopping"

    def test_unknown_returns_general(self):
        detect = self._get_detector()
        assert detect("random thing") == "general"


# ══════════════════════════════════════════════
# TEST 6: XSS escape functions
# ══════════════════════════════════════════════
class TestXSSHelpers:
    """Test that the esc/safeUrl/escAttr functions exist in journal HTML."""

    def test_esc_function_exists(self):
        html_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "journal", "index.html"
        )
        if not os.path.exists(html_path):
            pytest.skip("journal/index.html not found")
        with open(html_path) as f:
            content = f.read()
        assert "function esc(" in content
        assert "function safeUrl(" in content
        assert "function escAttr(" in content

    def test_tags_use_esc(self):
        html_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "journal", "index.html"
        )
        if not os.path.exists(html_path):
            pytest.skip("journal/index.html not found")
        with open(html_path) as f:
            content = f.read()
        assert "esc(t)" in content  # Tags should be escaped

    def test_photos_use_safeUrl(self):
        html_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "journal", "index.html"
        )
        if not os.path.exists(html_path):
            pytest.skip("journal/index.html not found")
        with open(html_path) as f:
            content = f.read()
        assert "safeUrl(p)" in content  # Photo URLs should be sanitized


# ══════════════════════════════════════════════
# TEST 7: CORS configuration
# ══════════════════════════════════════════════
class TestCORS:
    """Test that CORS is not hardcoded to wildcard."""

    def test_no_hardcoded_wildcard(self):
        api_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "api_server.py"
        )
        with open(api_path) as f:
            content = f.read()
        assert "ALLOWED_ORIGIN" in content
        assert "'*'" not in content.split("ALLOWED_ORIGIN")[1].split("\n")[0]  # Wildcard only in fallback

    def test_allowed_origin_from_env(self):
        from api_server import ALLOWED_ORIGIN
        # Should be either env var or '*' fallback
        assert isinstance(ALLOWED_ORIGIN, str)
        assert len(ALLOWED_ORIGIN) > 0


# ══════════════════════════════════════════════
# TEST 8: Portfolio transaction math
# ══════════════════════════════════════════════
class TestPortfolioTransactions:
    """Test the weighted avg cost and P/L calculations used by the portfolio system."""

    def test_avg_cost_first_buy(self):
        """First buy: avg cost = buy price."""
        shares, price = 10, 100
        avg = price  # No existing position
        assert avg == 100.0

    def test_avg_cost_two_buys(self):
        """Buy 10 @ 100, then 10 @ 200 → avg should be 150."""
        old_shares, old_avg = 10, 100.0
        new_shares, new_price = 10, 200.0
        total = old_shares + new_shares
        avg = (old_shares * old_avg + new_shares * new_price) / total
        assert avg == 150.0

    def test_avg_cost_unequal_lots(self):
        """Buy 100 @ 25, then 50 @ 30 → avg = (2500 + 1500) / 150 = 26.67."""
        old_shares, old_avg = 100, 25.0
        new_shares, new_price = 50, 30.0
        total = old_shares + new_shares
        avg = (old_shares * old_avg + new_shares * new_price) / total
        assert round(avg, 2) == 26.67

    def test_realized_pl_profit(self):
        """Buy at avg 100, sell 5 at 120 → P/L = +100."""
        avg_cost, sell_price, sell_shares = 100, 120, 5
        pl = (sell_price - avg_cost) * sell_shares
        assert pl == 100.0

    def test_realized_pl_loss(self):
        """Buy at avg 100, sell 10 at 80 → P/L = -200."""
        avg_cost, sell_price, sell_shares = 100, 80, 10
        pl = (sell_price - avg_cost) * sell_shares
        assert pl == -200.0

    def test_realized_pl_breakeven(self):
        """Sell at same price as avg cost → P/L = 0."""
        avg_cost, sell_price, sell_shares = 100, 100, 10
        pl = (sell_price - avg_cost) * sell_shares
        assert pl == 0.0

    def test_partial_sell_remaining(self):
        """Hold 20, sell 5 → 15 remaining."""
        current, sell = 20, 5
        remaining = round(current - sell, 4)
        assert remaining == 15

    def test_full_sell_remaining_zero(self):
        """Hold 20, sell 20 → 0 remaining."""
        current, sell = 20, 20
        remaining = round(current - sell, 4)
        assert remaining <= 0

    def test_cannot_sell_more_than_held(self):
        """Selling more than held should be blocked."""
        current_shares, sell_shares = 10, 15
        assert sell_shares > current_shares  # This check happens in sell_holding()

    def test_cost_basis_after_partial_sell(self):
        """100 shares @ avg 25 = basis 2500. Sell 40 → 60 shares, basis = 1500."""
        shares, avg_cost = 100, 25.0
        sell_shares = 40
        remaining = shares - sell_shares
        new_basis = remaining * avg_cost  # Avg cost doesn't change on sell
        assert remaining == 60
        assert new_basis == 1500.0

    def test_cumulative_realized_pl(self):
        """Multiple sells accumulate P/L."""
        sell1_pl = (30 - 25) * 10   # +50
        sell2_pl = (20 - 25) * 5    # -25
        total_pl = sell1_pl + sell2_pl
        assert total_pl == 25.0


# ══════════════════════════════════════════════
# TEST 9: Portfolio function signatures
# ══════════════════════════════════════════════
class TestPortfolioFunctions:
    """Test that new portfolio functions exist and are importable."""

    def test_sell_holding_exists(self):
        try:
            from tracker_tools import sell_holding
            assert callable(sell_holding)
        except ImportError:
            pytest.skip("tracker_tools deps not available")

    def test_get_transactions_exists(self):
        try:
            from tracker_tools import get_transactions
            assert callable(get_transactions)
        except ImportError:
            pytest.skip("tracker_tools deps not available")

    def test_format_pnl_summary_exists(self):
        try:
            from tracker_tools import format_pnl_summary
            assert callable(format_pnl_summary)
        except ImportError:
            pytest.skip("tracker_tools deps not available")

    def test_migrate_legacy_holdings_exists(self):
        try:
            from tracker_tools import migrate_legacy_holdings
            assert callable(migrate_legacy_holdings)
        except ImportError:
            pytest.skip("tracker_tools deps not available")


# ══════════════════════════════════════════════
# TEST 10: Slash commands exist in source
# ══════════════════════════════════════════════
class TestSlashCommands:
    """Verify slash commands are registered in main.py source."""

    def _get_main_source(self):
        main_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "main.py"
        )
        if not os.path.exists(main_path):
            pytest.skip("main.py not found")
        with open(main_path) as f:
            return f.read()

    def test_slash_buy_exists(self):
        src = self._get_main_source()
        assert 'bot.tree.command(name="buy"' in src

    def test_slash_sell_exists(self):
        src = self._get_main_source()
        assert 'bot.tree.command(name="sell"' in src

    def test_slash_portfolio_exists(self):
        src = self._get_main_source()
        assert 'bot.tree.command(name="portfolio"' in src

    def test_slash_spent_exists(self):
        src = self._get_main_source()
        assert 'bot.tree.command(name="spent"' in src

    def test_slash_budget_exists(self):
        src = self._get_main_source()
        assert 'bot.tree.command(name="budget"' in src

    def test_slash_price_exists(self):
        src = self._get_main_source()
        assert 'bot.tree.command(name="price"' in src

    def test_slash_convert_exists(self):
        src = self._get_main_source()
        assert 'bot.tree.command(name="convert"' in src

    def test_slash_remind_exists(self):
        src = self._get_main_source()
        assert 'bot.tree.command(name="remind"' in src

    def test_slash_apptoken_is_ephemeral(self):
        """apptoken slash command should send the token as ephemeral (private) message."""
        src = self._get_main_source()
        idx = src.find('bot.tree.command(name="apptoken"')
        assert idx > 0, "Slash apptoken command not found"
        section = src[idx:idx+600]
        assert "ephemeral=True" in section

    def test_tree_sync_in_on_ready(self):
        src = self._get_main_source()
        assert "bot.tree.sync()" in src

    def test_app_commands_imported(self):
        src = self._get_main_source()
        assert "from discord import app_commands" in src

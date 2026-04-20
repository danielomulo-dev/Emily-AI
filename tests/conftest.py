"""
Test fixtures and environment setup.

main.py constructs API clients at module-import time (Gemini, Claude, MongoDB).
In a test environment without real credentials, the import blows up. This
file sets dummy env vars before the test collector loads main.py, so tests
can reach pure functions like _detect_expense_category, _route_to_model, etc.
"""
import os
import sys

# Any env var consumed at top-level of a module must be set BEFORE that module
# is imported. pytest loads conftest.py before collecting tests, so this runs
# in time.
os.environ.setdefault("GEMINI_API_KEY", "test-dummy-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")
os.environ.setdefault("DISCORD_TOKEN", "test-dummy-token")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("BOT_OWNER_ID", "123456789")
os.environ.setdefault("JOURNAL_APP_URL", "https://example.com")

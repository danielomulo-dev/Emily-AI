import os
import logging
import re
import requests
import time
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")

# ── Price cache (avoids hitting APIs repeatedly for same stock) ──
_price_cache = {}  # {symbol: {"result": ..., "time": timestamp}}
CACHE_TTL = 3600   # 1 hour cache

# Browser-like headers for scraper fallbacks
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# NSE tickers mapped to full names
NSE_TICKERS = {
    "SCOM": "Safaricom", "KCB": "KCB Group", "EQTY": "Equity Group",
    "COOP": "Co-operative Bank", "ABSA": "ABSA Bank Kenya", "SBIC": "Stanbic Holdings",
    "NCBA": "NCBA Group", "DTB": "Diamond Trust Bank", "IMH": "I&M Holdings",
    "BKG": "BK Group", "HF": "HF Group", "CIC": "CIC Insurance",
    "BRIT": "Britam Holdings", "JUB": "Jubilee Holdings", "LKN": "Liberty Kenya",
    "KNRE": "Kenya Reinsurance", "EABL": "East African Breweries",
    "BAT": "BAT Kenya", "BOC": "BOC Kenya", "CARB": "Carbacid Investments",
    "BAMB": "Bamburi Cement", "ARM": "ARM Cement", "CRG": "Car & General",
    "KEGN": "KenGen", "KPLC": "Kenya Power", "TOTAL": "TotalEnergies Kenya",
    "UMME": "Umeme", "AIRTEL": "Airtel Africa",
    "CTUM": "Centum Investment", "ICDC": "ICDC", "OCH": "Olympia Capital",
    "SASN": "Sasini", "KUKZ": "Kakuzi", "LIMT": "Limuru Tea",
    "WTK": "WPP Scangroup", "REA": "Rea Vipingo", "EGAD": "Eaagads",
    "KAPC": "Kapchorua Tea", "HOME": "Home Afrika",
    "UCHM": "Uchumi", "SGL": "Standard Group", "NMG": "Nation Media",
    "TPS": "TPS Eastern Africa", "SCAN": "WPP Scangroup", "HAFR": "Flame Tree Group",
    "FIRE": "Flame Tree Group", "KENO": "KenolKobil", "MSC": "Nairobi Securities",
    "EVRD": "Eveready East Africa", "WILL": "Williamson Tea",
}


# ──────────────────────────────────────────────
# SOURCE 1: Alpha Vantage (primary for everything)
# ──────────────────────────────────────────────
def _fetch_from_alphavantage(symbol, is_nse):
    """Fetch stock data from Alpha Vantage API."""
    if not ALPHA_VANTAGE_KEY:
        logger.warning("No ALPHA_VANTAGE_KEY set, skipping Alpha Vantage")
        return None

    try:
        # NSE uses .NRB suffix on Alpha Vantage
        lookup = f"{symbol}.NRB" if is_nse else symbol

        url = "https://www.alphavantage.co/query"
        params = {
            "function": "GLOBAL_QUOTE",
            "symbol": lookup,
            "apikey": ALPHA_VANTAGE_KEY,
        }

        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # Check for rate limit or error
        if "Note" in data or "Information" in data:
            logger.warning(f"Alpha Vantage rate limit: {data.get('Note', data.get('Information', ''))}")
            return None

        quote = data.get("Global Quote", {})
        if not quote or "05. price" not in quote:
            logger.warning(f"No Alpha Vantage data for {lookup}")
            return None

        price = float(quote["05. price"])
        change = float(quote.get("09. change", 0))
        change_pct_raw = quote.get("10. change percent", "0%")
        change_pct = float(change_pct_raw.replace("%", ""))

        name = NSE_TICKERS.get(symbol, symbol) if is_nse else symbol
        currency = "KES" if is_nse else "USD"

        return {
            "price": price,
            "currency": currency,
            "name": name,
            "change": change,
            "change_pct": change_pct,
            "source": "alphavantage",
        }

    except Exception as e:
        logger.error(f"Alpha Vantage failed for {symbol}: {e}")
        return None


# ──────────────────────────────────────────────
# SOURCE 2: AFX scraper (NSE fallback)
# ──────────────────────────────────────────────
def _fetch_nse_from_afx(symbol):
    """Scrape NSE stock data from afx.kwayisi.org."""
    try:
        url = f"https://afx.kwayisi.org/nseke/{symbol.lower()}/"
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        rows = soup.find_all("tr")
        data = {}
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True).lower()
                val = cells[1].get_text(strip=True)
                data[key] = val

        price_str = data.get("last trade", data.get("close", data.get("previous close", "")))
        if not price_str:
            header = soup.find("h2")
            if header:
                price_match = re.search(r'([\d,]+\.?\d*)', header.get_text())
                if price_match:
                    return {
                        "price": float(price_match.group(1).replace(",", "")),
                        "currency": "KES",
                        "name": NSE_TICKERS.get(symbol.upper(), symbol),
                        "source": "afx",
                    }
            return None

        price = float(re.sub(r'[^\d.]', '', price_str))
        result = {
            "price": price,
            "currency": "KES",
            "name": NSE_TICKERS.get(symbol.upper(), symbol),
            "source": "afx",
        }

        change_str = data.get("change", "")
        if change_str:
            try:
                change_val = float(re.sub(r'[^\d.\-]', '', change_str))
                if "-" in change_str:
                    change_val = -abs(change_val)
                result["change"] = change_val
            except ValueError:
                pass

        change_pct_str = data.get("% change", data.get("change %", ""))
        if change_pct_str:
            try:
                pct_val = float(re.sub(r'[^\d.\-]', '', change_pct_str))
                if "-" in change_pct_str:
                    pct_val = -abs(pct_val)
                result["change_pct"] = pct_val
            except ValueError:
                pass

        return result

    except Exception as e:
        logger.error(f"AFX scrape failed for {symbol}: {e}")
        return None


# ──────────────────────────────────────────────
# SOURCE 3: MyStocks scraper (NSE last resort)
# ──────────────────────────────────────────────
def _fetch_nse_from_mystocks(symbol):
    """Fallback scraper using mystocks.co.ke."""
    try:
        url = f"https://live.mystocks.co.ke/stock={symbol.upper()}"
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        price_el = soup.find(class_=re.compile(r"price|last", re.IGNORECASE))
        if price_el:
            price_match = re.search(r'([\d,]+\.?\d*)', price_el.get_text())
            if price_match:
                return {
                    "price": float(price_match.group(1).replace(",", "")),
                    "currency": "KES",
                    "name": NSE_TICKERS.get(symbol.upper(), symbol),
                    "source": "mystocks",
                }
        return None
    except Exception as e:
        logger.error(f"MyStocks scrape failed for {symbol}: {e}")
        return None


# ──────────────────────────────────────────────
# FORMATTER
# ──────────────────────────────────────────────
def _format_stock_result(symbol, result, is_nse):
    """Format stock data into a Discord message."""
    price = result["price"]
    currency = result["currency"]
    name = result.get("name", symbol)
    label = f"{name} ({symbol})" if name != symbol else symbol

    header = f"📈 **{label} (NSE)**\n" if is_nse else f"📈 **{label}**\n"
    price_line = f"**Price:** {price:,.2f} {currency}\n"

    change = result.get("change")
    change_pct = result.get("change_pct")

    if change is not None and change_pct is not None:
        arrow = "🟢" if change >= 0 else "🔴"
        sign = "+" if change >= 0 else ""
        change_line = f"{arrow} Change: {sign}{change:,.2f} ({sign}{change_pct:.2f}%)"
        return header + price_line + change_line
    else:
        return header + price_line.rstrip()


# ──────────────────────────────────────────────
# MAIN FUNCTION
# ──────────────────────────────────────────────
def get_stock_price(symbol):
    """
    Gets live price for Stocks, Crypto, or Forex.
    Results are cached for 1 hour to avoid burning API quota.

    Fetch order:
      NSE stocks:    Alpha Vantage → AFX scraper → MyStocks scraper
      Global stocks: Alpha Vantage
    """
    try:
        # 1. Clean the symbol
        symbol = symbol.replace('[', '').replace(']', '').replace('STOCK:', '').strip().upper()

        # 1b. Check cache first
        cache_key = symbol
        if cache_key in _price_cache:
            cached = _price_cache[cache_key]
            if time.time() - cached["time"] < CACHE_TTL:
                logger.info(f"Cache hit for {symbol} (age: {int(time.time() - cached['time'])}s)")
                return cached["result"]

        # 2. Determine if NSE
        is_nse = False
        base_symbol = symbol.replace(".NR", "").replace(".NRB", "")

        if base_symbol in NSE_TICKERS:
            is_nse = True
        elif symbol.endswith(".NR") or symbol.endswith(".NRB"):
            is_nse = True

        logger.info(f"Fetching: {base_symbol} (NSE: {is_nse})")

        # 3. Alpha Vantage first (works for both NSE and global)
        result = _fetch_from_alphavantage(base_symbol, is_nse)
        if result:
            logger.info(f"Got {base_symbol} from Alpha Vantage")
            formatted = _format_stock_result(base_symbol, result, is_nse)
            _price_cache[cache_key] = {"result": formatted, "time": time.time()}
            return formatted

        # 4. NSE fallbacks
        if is_nse:
            result = _fetch_nse_from_afx(base_symbol)
            if result:
                logger.info(f"Got {base_symbol} from AFX")
                formatted = _format_stock_result(base_symbol, result, is_nse)
                _price_cache[cache_key] = {"result": formatted, "time": time.time()}
                return formatted

            result = _fetch_nse_from_mystocks(base_symbol)
            if result:
                logger.info(f"Got {base_symbol} from MyStocks")
                formatted = _format_stock_result(base_symbol, result, is_nse)
                _price_cache[cache_key] = {"result": formatted, "time": time.time()}
                return formatted

            return f"*(Manze, I couldn't find live prices for {base_symbol} on NSE right now. Market might be closed.)*"

        # 5. Global stock not found
        return f"*(Manze, I couldn't find live prices for {base_symbol} right now.)*"

    except Exception as e:
        logger.error(f"Stock error for {symbol}: {e}")
        return None

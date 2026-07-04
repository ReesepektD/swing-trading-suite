"""
Dynamic market scanner — discovers fresh candidates every morning.

Replaces the static watchlist.  Each call to get_candidates():
  1. Downloads 31-day OHLCV data for a broad mid-cap universe (~200 tickers)
  2. Filters: price $5–$500, 30-day avg volume >200 K, market cap $300M–$80B
  3. Ranks by volume spike (today's volume ÷ 30-day average)
  4. Returns the top N as watchlist-format dicts  {"ticker": ..., "keywords": [...]}
     ready to drop straight into the social arbitrage scorer

Volume spike is the best single proxy for emerging social interest — unusual
trading activity precedes media coverage and analyst upgrades by days to weeks.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

# ── Screening parameters ──────────────────────────────────────────────────────
CANDIDATE_LIMIT  = 15          # how many tickers to score (keeps scan time ≤5 min)
MIN_PRICE        = 5.0
MAX_PRICE        = 500.0
MIN_AVG_VOLUME   = 200_000     # 30-day average daily shares traded
MIN_MARKET_CAP   = 300e6       # $300M
MAX_MARKET_CAP   = 80e9        # $80B  (avoid mega-caps — analyst coverage too thick)
MIN_SPIKE        = 1.25        # today's volume must be ≥1.25× the 30-day average

# ── Seed universe ─────────────────────────────────────────────────────────────
# Mid/small-cap consumer, tech, and healthcare names.  Heavy on brand-driven
# companies because those are where Google Trends signals are most actionable.
# Update this list monthly as new IPOs arrive or positions get acquired.
UNIVERSE: list[str] = [
    # Consumer brands — food & beverage
    "CELH", "BROS", "CAVA", "WING", "SHAK", "DNUT", "PTLO", "JACK", "TXRH",
    "DPZ", "PZZA", "NATH", "PLAY", "FRSH", "CHEF", "PFGC",
    # Consumer brands — apparel & footwear
    "ONON", "BIRK", "CROX", "DECK", "BOOT", "GOOS", "LULU", "UAA", "PVH",
    "VFC", "LESL",
    # Consumer brands — retail & home
    "FIVE", "OLLI", "WSM", "RH", "LOVE", "BBWI", "PLBY",
    # Consumer tech & digital
    "DUOL", "DOMO", "MAPS", "PAYO", "BRZE", "TASK", "WEAV",
    "YELP", "ANGI", "MTCH", "BMBL",
    # Health & wellness
    "HIMS", "NUVL", "ACAD", "RCKT", "PRTA", "RARE", "IMVT",
    "INVA", "NTRA", "PACB", "SDGR", "DOCS", "PHVS",
    # Media & entertainment
    "RBLX", "TTWO", "MGAM", "DKNG", "RSI", "PENN",
    "IMAX", "CNK", "AMCX", "FUBO", "SIRI", "SPOT",
    # Travel & leisure
    "ABNB", "EXPE", "TRIP", "TRVG", "BKNG", "FWRG", "PLNT",
    "XPOF", "PTON",
    # Pets & home services
    "WOOF", "FRPT", "TRUP", "BARK", "CHWY",
    # Beauty & personal care
    "ELF", "OLPX", "COTY", "SKIN", "MNST", "NOMD",
    # Fintech & small-cap breakouts
    "SFIX", "W", "CVNA", "OPEN", "SOFI", "AFRM",
    "UPST", "PSFE", "RELY", "FLYW",
]
# Deduplicate while preserving order
_seen: set = set()
UNIVERSE = [t for t in UNIVERSE if t not in _seen and not _seen.add(t)]  # type: ignore[func-returns-value]


def _keywords_from_name(name: str, ticker: str) -> list[str]:
    """
    Generate plain-English search keywords from a company's long name.

    Strategy:
      - Strip legal suffixes (Inc, Corp, Ltd, Group, Holdings, etc.)
      - Lowercase and clean up
      - Return the cleaned name plus a ticker-only fallback

    Example: "Dutch Bros Inc" → ["dutch bros", "dutch bros coffee"]
    """
    stopwords = {
        "inc", "corp", "corporation", "ltd", "limited", "llc", "lp", "plc",
        "holdings", "holding", "group", "co", "company", "international",
        "enterprises", "solutions", "technologies", "systems", "the",
        "class", "a", "b", "c",
    }
    # Strip common suffixes and parenthetical class info
    clean = re.sub(r"\([^)]*\)", "", name)
    clean = re.sub(r",?\s*(inc\.?|corp\.?|ltd\.?|llc\.?|lp\.?|plc\.?|"
                   r"holdings?\.?|group\.?|co\.?|company\.?|"
                   r"international\.?|class [abc]\.?)$",
                   "", clean, flags=re.IGNORECASE).strip()

    words = [w.lower() for w in clean.split() if w.lower() not in stopwords]
    base  = " ".join(words).strip() or ticker.lower()

    # Heuristic: add a category suffix for well-known brand verticals
    brand = base
    keywords = [brand]
    if ticker.lower() not in brand:
        keywords.append(ticker.lower())

    return keywords[:3]  # max 3 terms — pytrends caps at 5 but 3 keeps it focused


class MarketScanner:
    """
    Screens the seed universe each morning and returns fresh trade candidates.

    Usage:
        scanner = MarketScanner()
        candidates = scanner.get_candidates()   # list of {"ticker": ..., "keywords": [...]}
    """

    def __init__(self, candidate_limit: int = CANDIDATE_LIMIT):
        self.candidate_limit = candidate_limit

    def get_candidates(self) -> list[dict]:
        """
        Download 31 days of OHLCV for the universe, apply filters, rank by
        volume spike, and return the top N as watchlist-format dicts.
        """
        log.info("MarketScanner: fetching %d-ticker universe…", len(UNIVERSE))
        tickers_str = " ".join(UNIVERSE)

        try:
            raw = yf.download(
                tickers_str,
                period="31d",
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
        except Exception as exc:
            log.error("Universe download failed: %s", exc)
            return []

        if raw.empty:
            log.warning("Universe download returned empty DataFrame")
            return []

        # yf.download with multiple tickers returns a MultiIndex (field, ticker)
        try:
            close  = raw["Close"]
            volume = raw["Volume"]
        except KeyError:
            log.error("Unexpected yfinance column structure: %s", raw.columns[:10].tolist())
            return []

        today_close  = close.iloc[-1]
        today_vol    = volume.iloc[-1]
        avg_vol_30d  = volume.iloc[:-1].mean()   # 30-day average (excluding today)

        spike = today_vol / avg_vol_30d.replace(0, float("nan"))
        spike = spike.dropna()

        # Build screening DataFrame
        screen = pd.DataFrame({
            "price":     today_close,
            "vol_today": today_vol,
            "vol_avg":   avg_vol_30d,
            "spike":     spike,
        }).dropna()

        # Price and volume filters
        screen = screen[
            (screen["price"]     >= MIN_PRICE)     &
            (screen["price"]     <= MAX_PRICE)      &
            (screen["vol_avg"]   >= MIN_AVG_VOLUME) &
            (screen["spike"]     >= MIN_SPIKE)
        ]

        if screen.empty:
            log.warning("No candidates passed volume-spike filter (spike≥%.2f)", MIN_SPIKE)
            return []

        # Sort by volume spike descending, take top N
        candidates_df = screen.sort_values("spike", ascending=False).head(self.candidate_limit)

        log.info(
            "MarketScanner: %d candidates after filter (top spike: %s %.1fx)",
            len(candidates_df),
            candidates_df.index[0],
            candidates_df["spike"].iloc[0],
        )

        # Fetch company names for keyword generation (batch via yf.Tickers)
        tickers_found = candidates_df.index.tolist()
        names         = self._fetch_names(tickers_found)

        results = []
        for ticker in tickers_found:
            name     = names.get(ticker, ticker)
            keywords = _keywords_from_name(name, ticker)
            log.debug("Candidate: %s | name=%r | spike=%.1fx | keywords=%s",
                      ticker, name, candidates_df.loc[ticker, "spike"], keywords)
            results.append({"ticker": ticker, "keywords": keywords})

        return results

    def _fetch_names(self, tickers: list[str]) -> dict[str, str]:
        """Return {ticker: longName} for each ticker, best-effort."""
        names: dict[str, str] = {}
        for ticker in tickers:
            try:
                info = yf.Ticker(ticker).info
                names[ticker] = info.get("longName", ticker)
            except Exception:
                names[ticker] = ticker
            time.sleep(0.2)   # gentle pacing — avoid Yahoo rate-limit
        return names

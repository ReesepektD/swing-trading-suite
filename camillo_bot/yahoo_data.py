"""
Yahoo Finance real-time data client.

Wraps yfinance in a clean, cacheable interface.  All prices are the live
"last trade" price from Yahoo's quote endpoint (fast_info.last_price),
not end-of-day close values.

Usage:
    from camillo_bot.yahoo_data import YahooDataClient
    ydc = YahooDataClient()
    price = ydc.get_price("AAPL")
    quotes = ydc.get_quotes(["AAPL", "TSLA", "NVDA"])
    bars  = ydc.get_intraday("AAPL", interval="5m")
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

# Simple in-process TTL cache so we don't hammer Yahoo on every call.
_PRICE_CACHE_TTL = 15   # seconds between live price refreshes per ticker
_QUOTE_CACHE_TTL = 30   # seconds for full quote objects


@dataclass
class Quote:
    ticker:         str
    last_price:     float
    bid:            float
    ask:            float
    prev_close:     float
    open_price:     float
    day_high:       float
    day_low:        float
    volume:         int
    market_cap:     Optional[float]
    change_pct:     float           # vs previous close, e.g. 0.023 = +2.3%
    is_market_open: bool
    timestamp:      float = field(default_factory=time.time)

    @property
    def mid(self) -> float:
        """Mid-price between bid and ask (fallback to last_price)."""
        if self.bid > 0 and self.ask > 0:
            return round((self.bid + self.ask) / 2, 4)
        return self.last_price


class YahooDataClient:
    """
    Real-time market data via Yahoo Finance (yfinance).

    Thread-safe for concurrent reads (each call is independent).
    In-process TTL cache keeps quote data fresh without hammering Yahoo.
    """

    def __init__(self):
        self._price_cache: dict[str, tuple[float, float]] = {}  # ticker → (price, ts)
        self._quote_cache: dict[str, tuple[Quote, float]] = {}  # ticker → (Quote, ts)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_price(self, ticker: str) -> float:
        """Return the live last-trade price for ticker."""
        cached_price, ts = self._price_cache.get(ticker, (None, 0.0))
        if cached_price is not None and (time.time() - ts) < _PRICE_CACHE_TTL:
            return cached_price

        price = self._fetch_price(ticker)
        self._price_cache[ticker] = (price, time.time())
        return price

    def get_quote(self, ticker: str) -> Quote:
        """Return a full real-time Quote for ticker."""
        cached_q, ts = self._quote_cache.get(ticker, (None, 0.0))
        if cached_q is not None and (time.time() - ts) < _QUOTE_CACHE_TTL:
            return cached_q

        q = self._fetch_quote(ticker)
        self._quote_cache[ticker] = (q, time.time())
        return q

    def get_quotes(self, tickers: list[str]) -> dict[str, Quote]:
        """Batch-fetch quotes for multiple tickers.

        Uses yf.download for efficiency when cache misses are present.
        Returns a dict of ticker → Quote.
        """
        now = time.time()
        fresh   = {t: self._quote_cache[t][0] for t in tickers
                   if t in self._quote_cache and (now - self._quote_cache[t][1]) < _QUOTE_CACHE_TTL}
        missing = [t for t in tickers if t not in fresh]

        if missing:
            batch = self._fetch_quotes_batch(missing)
            for t, q in batch.items():
                self._quote_cache[t] = (q, now)
            fresh.update(batch)

        return fresh

    def get_intraday(self, ticker: str, interval: str = "5m",
                     period: str = "1d") -> pd.DataFrame:
        """Return intraday OHLCV bars.

        interval: '1m', '2m', '5m', '15m', '30m', '60m', '90m'
        period:   '1d', '5d', '1mo'
        """
        try:
            t = yf.Ticker(ticker)
            df = t.history(period=period, interval=interval, auto_adjust=True)
            if df.empty:
                log.warning("No intraday data for %s (%s/%s)", ticker, interval, period)
            return df
        except Exception as exc:
            log.warning("Intraday fetch failed for %s: %s", ticker, exc)
            return pd.DataFrame()

    def get_history(self, ticker: str, period: str = "3mo",
                    interval: str = "1d") -> pd.DataFrame:
        """Return historical OHLCV bars (daily by default)."""
        try:
            t = yf.Ticker(ticker)
            df = t.history(period=period, interval=interval, auto_adjust=True)
            return df
        except Exception as exc:
            log.warning("History fetch failed for %s: %s", ticker, exc)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Internal fetch helpers
    # ------------------------------------------------------------------

    def _fetch_price(self, ticker: str) -> float:
        """Fastest possible single-ticker live price via fast_info."""
        try:
            fi = yf.Ticker(ticker).fast_info
            price = fi.last_price
            if price and price > 0:
                log.debug("YF price %s = $%.4f", ticker, price)
                return float(price)
        except Exception as exc:
            log.debug("fast_info failed for %s: %s — falling back to history", ticker, exc)

        # Fallback: last close from 5-day history
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as exc:
            log.warning("History fallback also failed for %s: %s", ticker, exc)

        raise ValueError(f"Cannot fetch price for {ticker}")

    def _fetch_quote(self, ticker: str) -> Quote:
        """Full quote using fast_info + info for bid/ask."""
        t  = yf.Ticker(ticker)
        fi = t.fast_info

        last_price = float(fi.last_price or 0)
        prev_close = float(fi.previous_close or 0)
        change_pct = ((last_price - prev_close) / prev_close) if prev_close else 0.0

        # bid/ask live from info (slightly slower but gives spread data)
        try:
            info = t.info
            bid  = float(info.get("bid", 0) or 0)
            ask  = float(info.get("ask", 0) or 0)
        except Exception:
            bid = ask = 0.0

        return Quote(
            ticker         = ticker,
            last_price     = last_price,
            bid            = bid,
            ask            = ask,
            prev_close     = prev_close,
            open_price     = float(fi.open or 0),
            day_high       = float(fi.day_high or 0),
            day_low        = float(fi.day_low or 0),
            volume         = int(fi.three_month_average_volume or 0),
            market_cap     = float(fi.market_cap) if fi.market_cap else None,
            change_pct     = change_pct,
            is_market_open = bool(getattr(fi, "market_state", "") in ("REGULAR", "PRE", "POST")),
        )

    def _fetch_quotes_batch(self, tickers: list[str]) -> dict[str, Quote]:
        """Batch fetch using yf.download for efficiency, then fill Quote objects."""
        results: dict[str, Quote] = {}
        try:
            raw = yf.download(
                tickers,
                period="2d",
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
        except Exception as exc:
            log.warning("Batch download failed: %s — falling back to individual fetches", exc)
            for t in tickers:
                try:
                    results[t] = self._fetch_quote(t)
                except Exception:
                    pass
            return results

        # yf.download returns multi-level columns when len(tickers) > 1
        single = len(tickers) == 1
        for ticker in tickers:
            try:
                if single:
                    close_series = raw["Close"]
                else:
                    close_series = raw["Close"][ticker]

                close_series = close_series.dropna()
                if close_series.empty:
                    continue

                last_price = float(close_series.iloc[-1])
                prev_close = float(close_series.iloc[-2]) if len(close_series) >= 2 else last_price
                change_pct = (last_price - prev_close) / prev_close if prev_close else 0.0

                results[ticker] = Quote(
                    ticker         = ticker,
                    last_price     = last_price,
                    bid            = 0.0,
                    ask            = 0.0,
                    prev_close     = prev_close,
                    open_price     = 0.0,
                    day_high       = 0.0,
                    day_low        = 0.0,
                    volume         = 0,
                    market_cap     = None,
                    change_pct     = change_pct,
                    is_market_open = False,
                )
            except Exception as exc:
                log.warning("Batch quote parse failed for %s: %s", ticker, exc)

        # Fill any tickers that the batch missed with individual fetches
        for ticker in tickers:
            if ticker not in results:
                try:
                    results[ticker] = self._fetch_quote(ticker)
                except Exception:
                    pass

        return results


# Module-level singleton — callers share the same cache
_default_client: Optional[YahooDataClient] = None


def get_client() -> YahooDataClient:
    """Return the module-level shared YahooDataClient instance."""
    global _default_client
    if _default_client is None:
        _default_client = YahooDataClient()
    return _default_client

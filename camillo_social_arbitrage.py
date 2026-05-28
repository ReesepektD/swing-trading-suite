#!/usr/bin/env python3
"""
Chris Camillo — Social Arbitrage Strategy Scanner
Based on "Laughing at Wall Street"

THESIS: Stocks are mispriced when cultural/social trends are visible to
everyday observers but have not yet been modeled by Wall Street analysts.
The edge is the information gap, not financial sophistication.

ENTRY: Trend accelerating + analyst coverage thin + price hasn't moved yet
EXIT:  Trend peaks (mainstream) OR analyst coverage rapidly expands
"""

import os
import random
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from pytrends.request import TrendReq

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

REDDIT_SUBREDDITS = ["wallstreetbets", "stocks", "investing", "StockMarket"]
TREND_TIMEFRAME   = "today 3-m"     # 90-day Google Trends window
REDDIT_POST_LIMIT = 75              # posts per subreddit per keyword
PYTRENDS_SLEEP    = 2.5             # seconds between Google Trends calls
BATCH_SIZE        = 2               # tickers per batch before a long pause
BATCH_PAUSE       = 45              # seconds between batches (avoids 429s)

SCORE_WEIGHTS = {
    "trend_velocity": 0.30,   # is the cultural trend accelerating?
    "analyst_gap":    0.25,   # is Wall Street ignoring this?
    "reddit_buzz":    0.25,   # is retail noticing?
    "price_lag":      0.20,   # has price moved yet?
}

# Conviction tiers (Camillo's sizing guidance)
TIERS = {
    "BUY":   (70, 100, "≤10% of portfolio — high conviction social arb"),
    "WATCH": (50,  69, "≤5%  of portfolio — monitor for catalyst"),
    "PASS":  (0,   49, "No position — trend not yet confirmed or overpriced"),
}


# ---------------------------------------------------------------------------
# DATA CLASSES
# ---------------------------------------------------------------------------

@dataclass
class ArbitrageSignal:
    ticker:               str
    keywords:             list
    trend_velocity_score: float = 0.0
    analyst_gap_score:    float = 0.0
    reddit_buzz_score:    float = 0.0
    price_lag_score:      float = 0.0
    composite_score:      float = 0.0
    signal:               str   = "PASS"
    notes:                list  = field(default_factory=list)

    @property
    def tier_guidance(self) -> str:
        for label, (lo, hi, guidance) in TIERS.items():
            if lo <= self.composite_score <= hi:
                return guidance
        return ""


# ---------------------------------------------------------------------------
# SCANNER
# ---------------------------------------------------------------------------

class SocialArbitrageScanner:
    """
    Four-factor Social Arbitrage scorer.

    Factor 1 — Trend Velocity   (Google Trends slope + acceleration)
    Factor 2 — Analyst Gap      (inverse analyst count + upside to target)
    Factor 3 — Reddit Buzz      (mention volume + VADER sentiment)
    Factor 4 — Price Lag        (trend rising but price hasn't responded)
    """

    def __init__(self, reddit_creds: Optional[dict] = None):
        self.pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 30))
        self._init_vader()
        self.reddit = self._init_reddit(reddit_creds)

    # ------------------------------------------------------------------
    # Init helpers
    # ------------------------------------------------------------------

    def _init_vader(self):
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            self.sia = SentimentIntensityAnalyzer()
        except ImportError:
            self.sia = None
            print("  [warn] vaderSentiment not installed — sentiment scoring disabled")

    def _init_reddit(self, creds: Optional[dict]):
        if not creds:
            return None
        try:
            import praw
            r = praw.Reddit(
                client_id=creds["client_id"],
                client_secret=creds["client_secret"],
                user_agent=creds.get("user_agent", "SocialArbitrageScanner/1.0"),
            )
            r.read_only = True
            return r
        except Exception as e:
            print(f"  [warn] Reddit init failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Factor 1 — Google Trend Velocity
    # ------------------------------------------------------------------

    def get_trend_velocity(self, keywords: list) -> tuple:
        """
        Measures the slope and recent acceleration of Google search interest.

        Camillo's core observation: cultural trends show up in search behavior
        weeks or months before they appear in earnings reports or analyst notes.
        """
        kws = keywords[:5]
        df  = pd.DataFrame()
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                time.sleep(random.uniform(2, 4))
                self.pytrends.build_payload(kws, timeframe=TREND_TIMEFRAME)
                df = self.pytrends.interest_over_time()
                break
            except Exception as e:
                is_last = attempt == max_attempts - 1
                if "429" in str(e) or "response with code 429" in str(e).lower():
                    if is_last:
                        log.warning("Trend rate-limited for %s — skipping (score=0)", kws[0])
                        return 0.0, pd.DataFrame()
                    backoff = (2 ** attempt) * random.uniform(10, 20)
                    print(f"      Trend 429, retrying in {backoff:.0f}s...")
                    time.sleep(backoff)
                else:
                    log.warning("Trend error for %s: %s", kws[0], e)
                    return 0.0, pd.DataFrame()
        if df.empty:
            return 0.0, pd.DataFrame()

        try:
            # Use the primary keyword column
            col = kws[0] if kws[0] in df.columns else df.columns[0]
            series = df[col].values.astype(float)

            if len(series) < 4:
                return 0.0, df

            x = np.arange(len(series))

            # Long-run slope (normalized per week)
            slope, _ = np.polyfit(x, series, 1)
            slope_score = float(np.clip(slope / 2.0 * 100, 0, 100))

            # Short-run acceleration: last 4 weeks vs prior 4 weeks
            recent = series[-4:].mean()
            prior  = series[-8:-4].mean() if len(series) >= 8 else series[:4].mean()
            accel  = (recent - prior) / (prior + 1e-9)
            accel_score = float(np.clip(accel * 100, 0, 100))

            score = round(slope_score * 0.5 + accel_score * 0.5, 1)
            return score, df

        except Exception as e:
            print(f"      Trend calc error: {e}")
            return 0.0, df

    # ------------------------------------------------------------------
    # Factor 2 — Analyst Coverage Gap
    # ------------------------------------------------------------------

    def get_analyst_gap_score(self, ticker: str) -> tuple:
        """
        Inverse coverage score: fewer analysts = larger information gap.

        Camillo targets stocks where cultural signals are clear to everyday
        observers but institutional research coverage hasn't caught up yet.
        """
        try:
            from camillo_bot.yahoo_data import get_client
            stock = yf.Ticker(ticker)
            info  = stock.info

            count  = info.get("numberOfAnalystOpinions", 0) or 0
            target = info.get("targetMeanPrice")

            # Use live price from YahooDataClient; fall back to info fields
            try:
                price = get_client().get_price(ticker)
            except Exception:
                price = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)

            # Fewer analysts → higher gap score
            if   count == 0:  coverage_score = 100
            elif count <= 2:  coverage_score = 90
            elif count <= 5:  coverage_score = 75
            elif count <= 10: coverage_score = 55
            elif count <= 20: coverage_score = 30
            else:             coverage_score = 10

            # Upside-to-target bonus (signals analysts are bullish but market lags)
            upside = 0.0
            if target and price and price > 0:
                upside = (target - price) / price * 100
            upside_bonus = float(np.clip(upside / 50 * 20, 0, 20))

            score = round(min(coverage_score + upside_bonus, 100), 1)
            meta  = {
                "analyst_count": count,
                "target_price":  target,
                "current_price": round(price, 4),
                "upside_pct":    round(upside, 1),
            }
            return score, meta

        except Exception as e:
            print(f"      Analyst gap error: {e}")
            return 50.0, {}

    # ------------------------------------------------------------------
    # Factor 3 — Reddit Buzz & Sentiment
    # ------------------------------------------------------------------

    def get_reddit_buzz_score(self, ticker: str, keywords: list) -> tuple:
        """
        Mention velocity + sentiment across retail investing communities.

        Camillo's framework doesn't rely on Reddit specifically, but retail
        social chatter is the best proxy for 'everyday observer' awareness.
        """
        if not self.reddit:
            return 50.0, {"note": "Reddit API not configured — neutral default"}

        mention_count   = 0
        sentiment_scores = []
        search_terms    = [ticker] + keywords[:2]

        for sub_name in REDDIT_SUBREDDITS[:3]:
            try:
                sub = self.reddit.subreddit(sub_name)
                for term in search_terms:
                    posts = sub.search(term, time_filter="week", limit=REDDIT_POST_LIMIT)
                    for post in posts:
                        mention_count += 1
                        if self.sia:
                            text  = f"{post.title} {post.selftext[:300]}"
                            score = self.sia.polarity_scores(text)["compound"]
                            sentiment_scores.append(score)
                time.sleep(0.25)
            except Exception:
                continue

        avg_sentiment = float(np.mean(sentiment_scores)) if sentiment_scores else 0.0

        # Volume component (0–60): 50+ mentions saturates
        volume_score = float(np.clip(mention_count / 50 * 60, 0, 60))
        # Sentiment component (0–40): maps [-1, 1] → [0, 40]
        sent_score   = (avg_sentiment + 1) / 2 * 40

        score = round(volume_score + sent_score, 1)
        label = ("positive" if avg_sentiment > 0.05
                 else "negative" if avg_sentiment < -0.05
                 else "neutral")

        meta = {
            "mentions_7d":     mention_count,
            "avg_sentiment":   round(avg_sentiment, 3),
            "sentiment_label": label,
        }
        return score, meta

    # ------------------------------------------------------------------
    # Factor 4 — Price Lag
    # ------------------------------------------------------------------

    def get_price_lag_score(self, ticker: str, trend_df: pd.DataFrame) -> tuple:
        """
        Detects the Camillo arbitrage gap: trend rising, price still flat.

        This is the core setup — cultural momentum has built but the stock
        hasn't repriced yet. The gap represents the profit opportunity.

        Uses the live last-trade price (via YahooDataClient) as the final
        data point so intraday moves are reflected in the score, not just
        yesterday's close.
        """
        try:
            from camillo_bot.yahoo_data import get_client
            ydc  = get_client()
            hist = ydc.get_history(ticker, period="3mo")

            if hist.empty:
                return 50.0, {}

            prices = hist["Close"].values.astype(float)

            # Replace the last close with the live price so the score
            # reflects what the market is doing right now.
            try:
                live_price = ydc.get_price(ticker)
                if live_price > 0:
                    prices[-1] = live_price
            except Exception:
                pass  # keep historical close if live fetch fails

            px_norm = prices / (prices[0] + 1e-9)
            x       = np.arange(len(px_norm))
            price_slope, _ = np.polyfit(x, px_norm, 1)

            trend_slope = 0.0
            if not trend_df.empty and len(trend_df) > 1:
                col     = trend_df.columns[0]
                t_vals  = trend_df[col].values.astype(float)
                t_norm  = t_vals / (t_vals.mean() + 1e-9)
                t_x     = np.arange(len(t_norm))
                trend_slope, _ = np.polyfit(t_x, t_norm, 1)

            # Positive gap = trend outpacing price (opportunity)
            gap   = trend_slope - price_slope
            score = float(np.clip(gap * 600, 0, 100))

            meta = {
                "price_3mo_pct": round((prices[-1] / prices[0] - 1) * 100, 1),
                "price_slope":   round(float(price_slope), 5),
                "trend_slope":   round(float(trend_slope), 5),
                "gap":           round(float(gap), 5),
                "live_price":    round(float(prices[-1]), 4),
            }
            return round(score, 1), meta

        except Exception as e:
            print(f"      Price lag error: {e}")
            return 50.0, {}

    # ------------------------------------------------------------------
    # Composite scorer
    # ------------------------------------------------------------------

    def score_ticker(self, ticker: str, keywords: list) -> ArbitrageSignal:
        sig = ArbitrageSignal(ticker=ticker, keywords=keywords)
        print(f"  Scanning {ticker}...")

        # 1. Google Trend velocity
        sig.trend_velocity_score, trend_df = self.get_trend_velocity(keywords)

        # 2. Analyst gap
        sig.analyst_gap_score, ameta = self.get_analyst_gap_score(ticker)
        if ameta.get("analyst_count", 99) <= 5:
            sig.notes.append(f"Thin coverage: {ameta['analyst_count']} analysts")
        if ameta.get("upside_pct", 0) > 15:
            sig.notes.append(f"Analyst upside: +{ameta['upside_pct']:.1f}%")

        # 3. Reddit buzz
        sig.reddit_buzz_score, rmeta = self.get_reddit_buzz_score(ticker, keywords)
        m7 = rmeta.get("mentions_7d", 0)
        if m7 > 0:
            sig.notes.append(f"Reddit 7d: {m7} mentions ({rmeta.get('sentiment_label','n/a')})")

        # 4. Price lag
        sig.price_lag_score, pmeta = self.get_price_lag_score(ticker, trend_df)
        if "price_3mo_pct" in pmeta:
            sig.notes.append(f"Price 3mo: {pmeta['price_3mo_pct']:+.1f}%")

        # Composite
        sig.composite_score = round(
            sig.trend_velocity_score * SCORE_WEIGHTS["trend_velocity"]
            + sig.analyst_gap_score  * SCORE_WEIGHTS["analyst_gap"]
            + sig.reddit_buzz_score  * SCORE_WEIGHTS["reddit_buzz"]
            + sig.price_lag_score    * SCORE_WEIGHTS["price_lag"],
            1,
        )

        # Signal
        if   sig.composite_score >= 70: sig.signal = "BUY"
        elif sig.composite_score >= 50: sig.signal = "WATCH"
        else:                           sig.signal = "PASS"

        return sig

    def scan_watchlist(self, watchlist: list) -> pd.DataFrame:
        """Scan all tickers in batches to respect Google Trends rate limits."""
        signals = []
        for i, item in enumerate(watchlist):
            try:
                s = self.score_ticker(item["ticker"], item["keywords"])
                signals.append(s)
            except Exception as e:
                print(f"  [error] {item['ticker']}: {e}")

            # Pause between batches — Google rate-limits per IP over short windows
            if (i + 1) % BATCH_SIZE == 0 and (i + 1) < len(watchlist):
                print(f"\n  [rate-limit pause] waiting {BATCH_PAUSE}s before next batch", end="", flush=True)
                for _ in range(BATCH_PAUSE):
                    time.sleep(1)
                    print(".", end="", flush=True)
                print()

        if not signals:
            return pd.DataFrame()

        rows = [{
            "Ticker":     s.ticker,
            "Signal":     s.signal,
            "Composite":  s.composite_score,
            "Trend":      s.trend_velocity_score,
            "AnalystGap": s.analyst_gap_score,
            "Reddit":     s.reddit_buzz_score,
            "PriceLag":   s.price_lag_score,
            "Guidance":   s.tier_guidance,
            "Notes":      " | ".join(s.notes),
        } for s in signals]

        return (pd.DataFrame(rows)
                  .sort_values("Composite", ascending=False)
                  .reset_index(drop=True))


# ---------------------------------------------------------------------------
# DISPLAY
# ---------------------------------------------------------------------------

FRAMEWORK_BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║         CHRIS CAMILLO — SOCIAL ARBITRAGE FRAMEWORK              ║
╠══════════════════════════════════════════════════════════════════╣
║  THESIS                                                          ║
║  Exploit the gap between what everyday people observe and        ║
║  what Wall Street analysts have priced into a stock.             ║
╠══════════════════════════════════════════════════════════════════╣
║  ENTRY CHECKLIST                                                 ║
║  ✓ Cultural trend is accelerating (Google Trends slope > 0)      ║
║  ✓ You observed the trend BEFORE reading about it financially    ║
║  ✓ Analyst coverage is thin (information gap exists)             ║
║  ✓ Price hasn't moved yet despite social momentum                ║
╠══════════════════════════════════════════════════════════════════╣
║  EXIT CHECKLIST                                                  ║
║  ✗ Trend peaks or plateaus on Google Trends                      ║
║  ✗ Analyst count rapidly expanding                               ║
║  ✗ Stock covered in mainstream financial media (CNBC, Bloomberg) ║
║  ✗ Your non-investor friends start talking about it              ║
╠══════════════════════════════════════════════════════════════════╣
║  POSITION SIZING (Camillo's guidance)                            ║
║  Score 70+ (BUY)   → up to 10% of portfolio                     ║
║  Score 50-69 (WATCH) → up to 5%                                 ║
║  Score <50  (PASS)   → no position                              ║
╚══════════════════════════════════════════════════════════════════╝
"""

SIGNAL_ICONS = {"BUY": "●", "WATCH": "◐", "PASS": "○"}


def print_results(df: pd.DataFrame):
    print("\n" + "═" * 72)
    print("  SOCIAL ARBITRAGE SCAN RESULTS")
    print("═" * 72)

    for _, row in df.iterrows():
        icon = SIGNAL_ICONS.get(row["Signal"], "?")
        print(f"\n  {icon} {row['Ticker']:<6}  [{row['Signal']}]  Score: {row['Composite']:.1f}/100")
        print(f"     Trend:{row['Trend']:5.1f}  AnalystGap:{row['AnalystGap']:5.1f}  "
              f"Reddit:{row['Reddit']:5.1f}  PriceLag:{row['PriceLag']:5.1f}")
        print(f"     Sizing: {row['Guidance']}")
        if row["Notes"]:
            print(f"     Notes: {row['Notes']}")

    print("\n" + "═" * 72)
    print(f"  Scan complete: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("═" * 72)


# ---------------------------------------------------------------------------
# MAIN — sample watchlist
# ---------------------------------------------------------------------------
#
# HOW TO BUILD YOUR OWN WATCHLIST:
#   1. Observe a trend in everyday life (a drink, shoe, app, habit)
#   2. Ask: "Has Wall Street noticed this yet?" (check analyst count)
#   3. Add the ticker + the NON-FINANCIAL keywords people would search
#      (e.g. "birkenstock sandals" not "BIRK earnings")
#   4. Run the scanner — let the score confirm or reject your thesis
#
# REQUIRED PACKAGES:
#   pip install yfinance pytrends pandas numpy vaderSentiment praw
#
# OPTIONAL — Reddit credentials (free at reddit.com/prefs/apps):
#   export REDDIT_CLIENT_ID=...
#   export REDDIT_CLIENT_SECRET=...

SAMPLE_WATCHLIST = [
    # Consumer brand riding a cultural wave
    {"ticker": "CELH",  "keywords": ["celsius drink", "celsius energy drink", "celsius gym"]},
    # Footwear trend (Camillo famously spotted Crocs, Lulu, Birks this way)
    {"ticker": "ONON",  "keywords": ["on running shoes", "on cloud shoes", "on running"]},
    {"ticker": "BIRK",  "keywords": ["birkenstock", "birkenstock sandals", "birkenstock trend"]},
    # App/platform with strong cultural identity
    {"ticker": "DUOL",  "keywords": ["duolingo", "duolingo streak", "learn spanish app"]},
    # Emerging consumer brand
    {"ticker": "FWRG",  "keywords": ["four winds", "fast casual healthy food"]},
]


def main():
    print(FRAMEWORK_BANNER)

    reddit_creds = None
    client_id     = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    if client_id and client_secret:
        reddit_creds = {"client_id": client_id, "client_secret": client_secret}
        print("Reddit API credentials found — full buzz scoring enabled.\n")
    else:
        print("Reddit API not configured — buzz scores default to 50 (neutral).")
        print("Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET env vars to enable.\n")

    scanner = SocialArbitrageScanner(reddit_creds=reddit_creds)

    print(f"Scanning {len(SAMPLE_WATCHLIST)} tickers...\n")
    results = scanner.scan_watchlist(SAMPLE_WATCHLIST)

    if results.empty:
        print("No results returned.")
        return

    print_results(results)

    out = f"social_arb_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    results.to_csv(out, index=False)
    print(f"\n  Results exported to: {out}\n")


if __name__ == "__main__":
    main()

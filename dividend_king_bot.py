#!/usr/bin/env python3
"""
Dividend King Swing Trading Bot

Targets Dividend Kings (S&P 500 companies with 50+ consecutive years of dividend
increases) and times entries using a four-factor swing-trade scoring model.

Four-factor scoring:
  1. Dividend health  (30%) — yield attractiveness, payout safety, 5-yr growth rate
  2. Trend template   (30%) — Minervini EMA/SMA alignment (7-condition checklist)
  3. Momentum/volume  (20%) — RSI position, volume surge, 20-day price action
  4. Value spread     (20%) — current yield vs. 5-yr historical mean (mean reversion)

Conviction tiers:
  BUY   (≥70) → up to 8% of portfolio per position
  WATCH (50–69) → monitor only, ≤4% if upgraded next scan
  PASS  (<50)  → no position

Risk rules:
  Stop loss:       -8% from entry (dividend support = tighter stop vs. growth stocks)
  Take profit #1:  +12% → sell 50%, let the rest ride
  Yield crisis:    exit if current yield compresses >1% below entry yield
  Max hold:        365 days (captures ~2 dividend payments)
  Portfolio halt:  no new entries if drawdown exceeds 15%

Run modes:
  python3 dividend_king_bot.py                    # dry run (scheduler loop)
  python3 dividend_king_bot.py --mode paper       # Alpaca paper trading
  python3 dividend_king_bot.py --mode live        # Alpaca live (requires env var)
  python3 dividend_king_bot.py --scan-now         # single scan and exit
  python3 dividend_king_bot.py --exit-check-now   # single exit check and exit
  python3 dividend_king_bot.py --status           # portfolio summary
  python3 dividend_king_bot.py --ticker PG TGT    # score specific tickers
"""

import argparse
import logging
import os
import sqlite3
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import pytz
import yfinance as yf

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("dividend_king_bot.log"),
    ],
)
log = logging.getLogger("dividend_king_bot")

# ---------------------------------------------------------------------------
# Dividend Kings universe
# Streak counts are approximate as of 2025; verify before live use.
# ---------------------------------------------------------------------------
DIVIDEND_KINGS: list[str] = [
    "AWR",   # American States Water       – 69 yrs (longest active streak)
    "YORW",  # York Water Company          – 207-yr continuous dividend history
    "DOV",   # Dover Corporation           – 69 yrs
    "NWN",   # Northwest Natural Gas       – 68 yrs
    "GPC",   # Genuine Parts               – 68 yrs
    "NDSN",  # Nordson Corporation         – 61 yrs
    "CINF",  # Cincinnati Financial        – 64 yrs
    "PG",    # Procter & Gamble            – 68 yrs
    "ITW",   # Illinois Tool Works         – 59 yrs
    "LOW",   # Lowe's Companies            – 62 yrs
    "JNJ",   # Johnson & Johnson           – 62 yrs
    "CL",    # Colgate-Palmolive           – 61 yrs
    "KO",    # Coca-Cola                   – 62 yrs
    "HRL",   # Hormel Foods                – 59 yrs
    "SYY",   # Sysco Corporation           – 54 yrs
    "WMT",   # Walmart                     – 52 yrs
    "TGT",   # Target Corporation          – 53 yrs
    "GWW",   # W.W. Grainger               – 53 yrs
    "ABT",   # Abbott Laboratories         – 53 yrs
    "ABBV",  # AbbVie (inherits Abbott)    – 53 yrs
    "ABM",   # ABM Industries              – 57 yrs
    "KMB",   # Kimberly-Clark              – 52 yrs
    "PEP",   # PepsiCo                     – 53 yrs
    "BDX",   # Becton Dickinson            – 53 yrs
    "PPG",   # PPG Industries              – 53 yrs
    "NUE",   # Nucor Corporation           – 51 yrs
    "CWT",   # California Water Service    – 57 yrs
    "MSEX",  # Middlesex Water             – 51 yrs
    "SCL",   # Stepan Company              – 57 yrs
    "GRC",   # Gorman-Rupp                 – 51 yrs
    "FRT",   # Federal Realty Inv Trust    – 57 yrs
    "LANC",  # Lancaster Colony            – 62 yrs
    "ATO",   # Atmos Energy                – 41 yrs
    "APD",   # Air Products & Chemicals    – 43 yrs
    "PNR",   # Pentair                     – 48 yrs
    "UVV",   # Universal Corporation       – 54 yrs
    "MKC",   # McCormick & Company         – 38 yrs
    "MSA",   # MSA Safety                  – 53 yrs
    "CBSH",  # Commerce Bancshares         – 56 yrs
    "CLX",   # Clorox                      – 47 yrs
    "FUL",   # H.B. Fuller                 – 55 yrs
    "EMR",   # Emerson Electric            – 47 yrs
    "BEN",   # Franklin Resources          – 44 yrs
    "TROW",  # T. Rowe Price               – 38 yrs
]

SCORE_WEIGHTS: dict[str, float] = {
    "dividend_health": 0.30,
    "trend_template":  0.30,
    "momentum":        0.20,
    "value_spread":    0.20,
}

CONVICTION_BUY   = 70
CONVICTION_WATCH = 50


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class DKConfig:
    watchlist: list = field(default_factory=lambda: list(DIVIDEND_KINGS))

    # Risk
    max_position_pct: float    = 0.08   # 8% of equity per BUY signal
    watch_position_pct: float  = 0.04   # 4% of equity for upgraded WATCH
    max_positions: int         = 15
    stop_loss_pct: float       = 0.08   # -8% hard stop
    take_profit_half_pct: float= 0.12   # +12% → sell 50%
    max_hold_days: int         = 365
    portfolio_halt_drawdown: float = 0.15  # halt entries if portfolio down 15%+

    # Fundamental filters
    min_yield_pct: float       = 1.0    # skip if current yield < 1%
    max_payout_ratio: float    = 0.80   # skip if payout ratio > 80%
    min_div_growth_5yr: float  = 2.0    # min 5-yr dividend growth CAGR %

    # Technical
    ema_fast: int   = 21
    ema_slow: int   = 50
    sma_150: int    = 150
    sma_200: int    = 200
    rsi_period: int = 14
    volume_lookback: int        = 50
    volume_surge_mult: float    = 1.5   # today vol ≥ 1.5× avg → surge

    # Yield mean-reversion window
    yield_spread_threshold: float = 1.0  # exit if yield compresses 1% below entry

    # Schedule (Eastern Time, weekdays only)
    scan_time: str            = "09:45"
    exit_check_times: list    = field(default_factory=lambda: ["12:00", "15:30"])
    summary_time: str         = "16:05"

    # Broker
    mode: str             = "dry"
    alpaca_api_key: str   = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", ""))
    alpaca_secret_key: str= field(default_factory=lambda: os.getenv("ALPACA_SECRET_KEY", ""))

    db_path: str = "dividend_king_bot.db"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class DividendMetrics:
    ticker: str
    current_yield_pct: float        # e.g. 2.5 = 2.5%
    five_yr_avg_yield_pct: float    # from yfinance fiveYearAvgDividendYield
    payout_ratio: float             # 0.0–1.0
    div_growth_1yr_pct: float
    div_growth_5yr_pct: float       # 5-yr CAGR
    consecutive_increase_yrs: int
    annual_dividend: float          # $ per share/year
    ex_div_date: Optional[str]


@dataclass
class DividendKingSignal:
    ticker: str
    dividend_health_score: float
    trend_template_score: float
    momentum_score: float
    value_spread_score: float
    composite_score: float
    signal: str                     # BUY | WATCH | PASS
    current_price: float
    current_yield_pct: float
    notes: str = ""


@dataclass
class DKPosition:
    ticker: str
    entry_date: str
    entry_price: float
    notional: float
    entry_score: float
    signal: str
    entry_yield_pct: float
    half_taken: bool = False
    last_exit_check: str = ""


@dataclass
class DKTradeLog:
    ticker: str
    action: str     # ENTRY | PARTIAL_EXIT | EXIT
    reason: str     # signal | stop_loss | take_profit | yield_crisis | max_hold
    price: float
    notional: float
    score: float
    timestamp: str


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
class DKDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_tables()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_tables(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    ticker           TEXT PRIMARY KEY,
                    entry_date       TEXT,
                    entry_price      REAL,
                    notional         REAL,
                    entry_score      REAL,
                    signal           TEXT,
                    entry_yield_pct  REAL,
                    half_taken       INTEGER DEFAULT 0,
                    last_exit_check  TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_log (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker    TEXT,
                    action    TEXT,
                    reason    TEXT,
                    price     REAL,
                    notional  REAL,
                    score     REAL,
                    timestamp TEXT
                )
            """)
            conn.commit()

    def save_position(self, pos: DKPosition):
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO positions
                  (ticker, entry_date, entry_price, notional, entry_score, signal,
                   entry_yield_pct, half_taken, last_exit_check)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                pos.ticker, pos.entry_date, pos.entry_price, pos.notional,
                pos.entry_score, pos.signal, pos.entry_yield_pct,
                int(pos.half_taken), pos.last_exit_check,
            ))
            conn.commit()

    def delete_position(self, ticker: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
            conn.commit()

    def load_positions(self) -> dict[str, DKPosition]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM positions").fetchall()
        return {
            r[0]: DKPosition(
                ticker=r[0], entry_date=r[1], entry_price=r[2],
                notional=r[3], entry_score=r[4], signal=r[5],
                entry_yield_pct=r[6], half_taken=bool(r[7]),
                last_exit_check=r[8],
            )
            for r in rows
        }

    def log_trade(self, trade: DKTradeLog):
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO trade_log (ticker, action, reason, price, notional, score, timestamp)
                VALUES (?,?,?,?,?,?,?)
            """, (
                trade.ticker, trade.action, trade.reason,
                trade.price, trade.notional, trade.score, trade.timestamp,
            ))
            conn.commit()

    def get_trade_history(self, limit: int = 50) -> list[DKTradeLog]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ticker,action,reason,price,notional,score,timestamp "
                "FROM trade_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            DKTradeLog(ticker=r[0], action=r[1], reason=r[2],
                       price=r[3], notional=r[4], score=r[5], timestamp=r[6])
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Broker abstractions
# ---------------------------------------------------------------------------
class DKDryRunBroker:
    def get_account_equity(self) -> float:
        return 100_000.0

    def place_buy_order(self, ticker: str, notional: float) -> dict:
        log.info("[DRY] BUY  %-6s  $%.2f notional", ticker, notional)
        return {"status": "dry_run"}

    def place_sell_order(self, ticker: str, qty: float, reason: str = "") -> dict:
        log.info("[DRY] SELL %-6s  qty=%.4f  reason=%s", ticker, qty, reason)
        return {"status": "dry_run"}

    def get_position(self, ticker: str) -> Optional[dict]:
        return None

    def get_all_positions(self) -> list[dict]:
        return []


class DKAlpacaBroker:
    def __init__(self, api_key: str, secret_key: str, live: bool = False):
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.trading.enums import OrderSide, TimeInForce
            from alpaca.trading.requests import MarketOrderRequest
        except ImportError:
            raise ImportError("pip install alpaca-py")

        self._client = TradingClient(api_key, secret_key, paper=not live)
        self._MarketOrderRequest = MarketOrderRequest
        self._OrderSide = OrderSide
        self._TimeInForce = TimeInForce
        log.info("[ALPACA-%s] broker ready.", "LIVE" if live else "PAPER")

    def get_account_equity(self) -> float:
        return float(self._client.get_account().equity)

    def place_buy_order(self, ticker: str, notional: float) -> dict:
        req = self._MarketOrderRequest(
            symbol=ticker, notional=round(notional, 2),
            side=self._OrderSide.BUY, time_in_force=self._TimeInForce.DAY,
        )
        order = self._client.submit_order(req)
        log.info("[ALPACA] BUY  %-6s  $%.2f  id=%s", ticker, notional, order.id)
        return {"status": "submitted", "order_id": str(order.id)}

    def place_sell_order(self, ticker: str, qty: float, reason: str = "") -> dict:
        req = self._MarketOrderRequest(
            symbol=ticker, qty=qty,
            side=self._OrderSide.SELL, time_in_force=self._TimeInForce.DAY,
        )
        order = self._client.submit_order(req)
        log.info("[ALPACA] SELL %-6s  qty=%.4f  reason=%s  id=%s",
                 ticker, qty, reason, order.id)
        return {"status": "submitted", "order_id": str(order.id)}

    def get_position(self, ticker: str) -> Optional[dict]:
        try:
            p = self._client.get_open_position(ticker)
            return {
                "ticker": ticker,
                "qty": float(p.qty),
                "market_value": float(p.market_value),
                "avg_entry_price": float(p.avg_entry_price),
            }
        except Exception:
            return None

    def get_all_positions(self) -> list[dict]:
        return [
            {
                "ticker": p.symbol, "qty": float(p.qty),
                "market_value": float(p.market_value),
                "avg_entry_price": float(p.avg_entry_price),
                "unrealized_pl_pct": float(p.unrealized_plpc) * 100,
            }
            for p in self._client.get_all_positions()
        ]


# ---------------------------------------------------------------------------
# Scanner — four-factor scoring engine
# ---------------------------------------------------------------------------
class DividendKingScanner:
    def __init__(self, config: DKConfig):
        self.cfg = config

    # --- Factor 1: Dividend Health (30%) ---

    def _score_dividend_health(self, m: DividendMetrics) -> float:
        # Yield sweet-spot: 2–4% ideal for dividend kings
        y = m.current_yield_pct
        if y < 1.0:
            yield_score = 0.0
        elif y < 2.0:
            yield_score = 40 + (y - 1.0) * 30
        elif y <= 4.0:
            yield_score = 70 + (y - 2.0) * 15
        else:
            yield_score = max(0.0, 100 - (y - 4.0) * 20)  # very high yield → distress risk

        # Payout ratio safety
        pr = m.payout_ratio
        if pr <= 0.0 or pr > 1.5:
            payout_score = 20.0
        elif pr <= 0.40:
            payout_score = 100.0
        elif pr <= 0.60:
            payout_score = 80.0
        elif pr <= 0.75:
            payout_score = 60.0
        elif pr <= 0.85:
            payout_score = 40.0
        else:
            payout_score = 10.0

        # 5-yr dividend growth CAGR
        dg = m.div_growth_5yr_pct
        if dg < 0:
            growth_score = 0.0
        elif dg < 3:
            growth_score = 30.0
        elif dg < 5:
            growth_score = 60.0
        elif dg < 8:
            growth_score = 85.0
        else:
            growth_score = 100.0

        # Consecutive-year streak bonus
        yrs = m.consecutive_increase_yrs
        streak_score = 100.0 if yrs >= 50 else (70.0 if yrs >= 25 else 40.0)

        return min(100.0, max(0.0,
            yield_score   * 0.35 +
            payout_score  * 0.30 +
            growth_score  * 0.25 +
            streak_score  * 0.10
        ))

    # --- Factor 2: Trend Template (30%) ---

    def _score_trend_template(self, hist: pd.DataFrame) -> float:
        if len(hist) < self.cfg.sma_200 + 30:
            return 50.0

        close = hist["Close"].dropna()
        price = float(close.iloc[-1])

        ema21  = float(close.ewm(span=self.cfg.ema_fast, adjust=False).mean().iloc[-1])
        ema50  = float(close.ewm(span=self.cfg.ema_slow, adjust=False).mean().iloc[-1])
        sma150 = float(close.rolling(self.cfg.sma_150).mean().iloc[-1])
        sma200 = float(close.rolling(self.cfg.sma_200).mean().iloc[-1])
        sma200_30d = float(close.rolling(self.cfg.sma_200).mean().iloc[-31])
        high_52w = float(close.tail(252).max())

        conditions = [
            price > ema21,
            price > ema50,
            price > sma150,
            price > sma200,
            sma150 > sma200,
            sma200 > sma200_30d,            # 200-day MA trending upward
            price >= high_52w * 0.75,        # within 25% of 52-week high
        ]
        return (sum(conditions) / 7) * 100.0

    # --- Factor 3: Momentum & Volume (20%) ---

    def _score_momentum(self, hist: pd.DataFrame) -> float:
        if len(hist) < 60:
            return 50.0

        close  = hist["Close"].dropna()
        volume = hist["Volume"].dropna()

        # RSI
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(self.cfg.rsi_period).mean()
        loss  = (-delta.clip(upper=0)).rolling(self.cfg.rsi_period).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = float((100 - 100 / (1 + rs)).iloc[-1])

        if rsi < 30:
            rsi_score = 20.0
        elif rsi < 50:
            rsi_score = 45.0
        elif rsi <= 70:
            rsi_score = 90.0
        elif rsi <= 80:
            rsi_score = 60.0
        else:
            rsi_score = 20.0

        # Volume surge vs. 50-day average
        avg_vol   = float(volume.iloc[-(self.cfg.volume_lookback + 1):-1].mean())
        today_vol = float(volume.iloc[-1])
        vol_ratio = today_vol / avg_vol if avg_vol > 0 else 1.0
        if vol_ratio >= self.cfg.volume_surge_mult:
            vol_score = 100.0
        else:
            vol_score = min(100.0, vol_ratio / self.cfg.volume_surge_mult * 100)

        # 20-day price momentum
        if len(close) >= 21:
            ret_20d = (float(close.iloc[-1]) - float(close.iloc[-21])) / float(close.iloc[-21]) * 100
        else:
            ret_20d = 0.0
        if ret_20d > 5:
            mom_score = 90.0
        elif ret_20d > 0:
            mom_score = 60 + ret_20d * 6
        else:
            mom_score = max(0.0, 60 + ret_20d * 8)

        return min(100.0, rsi_score * 0.40 + vol_score * 0.30 + mom_score * 0.30)

    # --- Factor 4: Yield Value Spread (20%) ---

    def _score_value_spread(self, m: DividendMetrics) -> float:
        if m.five_yr_avg_yield_pct <= 0:
            return 50.0
        # Positive spread = current yield > historical avg = price is depressed = value entry
        spread = (m.current_yield_pct - m.five_yr_avg_yield_pct) / m.five_yr_avg_yield_pct * 100
        if spread >= 30:
            return 95.0
        elif spread >= 15:
            return 80.0
        elif spread >= 5:
            return 65.0
        elif spread >= -5:
            return 50.0
        elif spread >= -15:
            return 35.0
        elif spread >= -25:
            return 20.0
        else:
            return 10.0

    # --- Data fetchers ---

    def _fetch_history(self, ticker: str) -> Optional[pd.DataFrame]:
        try:
            hist = yf.Ticker(ticker).history(period="2y")
            return hist if len(hist) >= 30 else None
        except Exception as e:
            log.warning("History fetch failed for %s: %s", ticker, e)
            return None

    def _fetch_dividend_metrics(self, ticker: str) -> Optional[DividendMetrics]:
        try:
            tk   = yf.Ticker(ticker)
            info = tk.info or {}

            current_yield     = float(info.get("dividendYield", 0) or 0) * 100
            five_yr_avg_yield = float(info.get("fiveYearAvgDividendYield", 0) or 0)
            payout_ratio      = float(info.get("payoutRatio", 0) or 0)
            trailing_div      = float(info.get("trailingAnnualDividendRate", 0) or 0)

            ex_ts      = info.get("exDividendDate")
            ex_div_date = (
                datetime.utcfromtimestamp(ex_ts).strftime("%Y-%m-%d") if ex_ts else None
            )

            # Derive growth and streak from dividend history
            div_growth_1yr = div_growth_5yr = 0.0
            consecutive_yrs = 50  # conservative default for known kings

            divs = tk.dividends
            if divs is not None and len(divs) >= 8:
                annual = divs.resample("YE").sum()
                vals   = annual.values

                if len(vals) >= 2 and vals[-2] > 0:
                    div_growth_1yr = (vals[-1] - vals[-2]) / vals[-2] * 100

                if len(vals) >= 6 and vals[-6] > 0:
                    div_growth_5yr = (vals[-1] / vals[-6]) ** (1 / 5) * 100 - 100

                # Count consecutive non-decreasing annual dividends (most recent first)
                rev = vals[::-1]
                consecutive_yrs = 0
                for i in range(1, len(rev)):
                    if rev[i] > 0 and rev[i - 1] >= rev[i]:
                        consecutive_yrs += 1
                    else:
                        break
                consecutive_yrs = max(consecutive_yrs, 25)  # floor at aristocrat level

            return DividendMetrics(
                ticker=ticker,
                current_yield_pct=current_yield,
                five_yr_avg_yield_pct=five_yr_avg_yield,
                payout_ratio=payout_ratio,
                div_growth_1yr_pct=div_growth_1yr,
                div_growth_5yr_pct=div_growth_5yr,
                consecutive_increase_yrs=consecutive_yrs,
                annual_dividend=trailing_div,
                ex_div_date=ex_div_date,
            )
        except Exception as e:
            log.warning("Dividend metrics fetch failed for %s: %s", ticker, e)
            return None

    # --- Score a single ticker ---

    def score_ticker(self, ticker: str) -> Optional[DividendKingSignal]:
        log.info("Scoring %-6s ...", ticker)

        hist    = self._fetch_history(ticker)
        metrics = self._fetch_dividend_metrics(ticker)

        if hist is None or metrics is None:
            return None

        price = float(hist["Close"].dropna().iloc[-1])

        # Hard filters — skip before scoring
        if metrics.current_yield_pct < self.cfg.min_yield_pct:
            return DividendKingSignal(
                ticker=ticker, dividend_health_score=0, trend_template_score=0,
                momentum_score=0, value_spread_score=0, composite_score=0,
                signal="PASS", current_price=price,
                current_yield_pct=metrics.current_yield_pct,
                notes=f"yield {metrics.current_yield_pct:.1f}% < min {self.cfg.min_yield_pct}%",
            )

        if metrics.payout_ratio > self.cfg.max_payout_ratio:
            return DividendKingSignal(
                ticker=ticker, dividend_health_score=0, trend_template_score=0,
                momentum_score=0, value_spread_score=0, composite_score=0,
                signal="PASS", current_price=price,
                current_yield_pct=metrics.current_yield_pct,
                notes=f"payout {metrics.payout_ratio:.0%} > max {self.cfg.max_payout_ratio:.0%}",
            )

        dh  = self._score_dividend_health(metrics)
        tt  = self._score_trend_template(hist)
        mom = self._score_momentum(hist)
        vs  = self._score_value_spread(metrics)

        composite = (
            dh  * SCORE_WEIGHTS["dividend_health"] +
            tt  * SCORE_WEIGHTS["trend_template"]  +
            mom * SCORE_WEIGHTS["momentum"]        +
            vs  * SCORE_WEIGHTS["value_spread"]
        )

        if composite >= CONVICTION_BUY:
            signal = "BUY"
        elif composite >= CONVICTION_WATCH:
            signal = "WATCH"
        else:
            signal = "PASS"

        notes = (
            f"DH={dh:.0f} TT={tt:.0f} MOM={mom:.0f} VS={vs:.0f} | "
            f"yield={metrics.current_yield_pct:.2f}% "
            f"payout={metrics.payout_ratio:.0%} "
            f"div5yr={metrics.div_growth_5yr_pct:.1f}%"
        )
        log.info("  %-6s %s  %.1f  [%s]", ticker, signal, composite, notes)

        return DividendKingSignal(
            ticker=ticker,
            dividend_health_score=dh,
            trend_template_score=tt,
            momentum_score=mom,
            value_spread_score=vs,
            composite_score=composite,
            signal=signal,
            current_price=price,
            current_yield_pct=metrics.current_yield_pct,
            notes=notes,
        )

    def scan_watchlist(self, tickers: Optional[list] = None) -> pd.DataFrame:
        tickers = tickers or self.cfg.watchlist
        rows = []
        for ticker in tickers:
            try:
                sig = self.score_ticker(ticker)
                if sig:
                    rows.append({
                        "ticker":          sig.ticker,
                        "signal":          sig.signal,
                        "composite_score": round(sig.composite_score, 1),
                        "div_health":      round(sig.dividend_health_score, 1),
                        "trend":           round(sig.trend_template_score, 1),
                        "momentum":        round(sig.momentum_score, 1),
                        "value_spread":    round(sig.value_spread_score, 1),
                        "price":           round(sig.current_price, 2),
                        "yield_pct":       round(sig.current_yield_pct, 2),
                        "notes":           sig.notes,
                    })
            except Exception as e:
                log.error("Error scoring %s: %s", ticker, e)

        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)
        return df


# ---------------------------------------------------------------------------
# Risk management
# ---------------------------------------------------------------------------
class DKRisk:
    def __init__(self, config: DKConfig):
        self.cfg = config

    def position_size(self, equity: float, score: float, signal: str) -> float:
        base_pct = self.cfg.max_position_pct if signal == "BUY" else self.cfg.watch_position_pct
        conviction_mult = min(1.0, score / 100 * 1.2)
        return equity * base_pct * conviction_mult

    def check_stop_loss(self, pos: DKPosition, price: float) -> bool:
        return price <= pos.entry_price * (1 - self.cfg.stop_loss_pct)

    def check_take_profit(self, pos: DKPosition, price: float) -> bool:
        return (not pos.half_taken and
                price >= pos.entry_price * (1 + self.cfg.take_profit_half_pct))

    def check_max_hold(self, pos: DKPosition) -> bool:
        entry_dt = datetime.strptime(pos.entry_date, "%Y-%m-%d")
        return (datetime.now() - entry_dt).days >= self.cfg.max_hold_days

    def check_yield_crisis(self, pos: DKPosition, current_yield_pct: float) -> bool:
        # Price ran up → yield compressed → mean-reversion risk → exit
        return current_yield_pct < (pos.entry_yield_pct - self.cfg.yield_spread_threshold)

    def portfolio_halted(self, initial_equity: float, current_equity: float) -> bool:
        return current_equity < initial_equity * (1 - self.cfg.portfolio_halt_drawdown)


# ---------------------------------------------------------------------------
# Executor — translates signals → broker orders
# ---------------------------------------------------------------------------
class DKExecutor:
    def __init__(self, broker, db: DKDatabase, risk: DKRisk, config: DKConfig):
        self.broker = broker
        self.db     = db
        self.risk   = risk
        self.cfg    = config

    def execute_entries(self, signals: list[DividendKingSignal],
                        positions: dict[str, DKPosition]):
        equity    = self.broker.get_account_equity()
        buy_sigs  = [s for s in signals
                     if s.signal == "BUY" and s.ticker not in positions]

        for sig in buy_sigs:
            if len(positions) >= self.cfg.max_positions:
                log.info("Max positions (%d) reached.", self.cfg.max_positions)
                break

            notional = self.risk.position_size(equity, sig.composite_score, sig.signal)
            if notional < 100:
                log.warning("%s: notional $%.2f too small, skipping.", sig.ticker, notional)
                continue

            self.broker.place_buy_order(sig.ticker, notional)

            pos = DKPosition(
                ticker=sig.ticker,
                entry_date=datetime.now().strftime("%Y-%m-%d"),
                entry_price=sig.current_price,
                notional=notional,
                entry_score=sig.composite_score,
                signal=sig.signal,
                entry_yield_pct=sig.current_yield_pct,
            )
            self.db.save_position(pos)
            positions[sig.ticker] = pos

            self.db.log_trade(DKTradeLog(
                ticker=sig.ticker, action="ENTRY", reason="signal",
                price=sig.current_price, notional=notional,
                score=sig.composite_score, timestamp=datetime.now().isoformat(),
            ))
            log.info("ENTRY %-6s @ $%.2f  notional=$%.0f  score=%.1f  yield=%.2f%%",
                     sig.ticker, sig.current_price, notional,
                     sig.composite_score, sig.current_yield_pct)

    def execute_exits(self, positions: dict[str, DKPosition]):
        for ticker, pos in list(positions.items()):
            try:
                tk      = yf.Ticker(ticker)
                hist    = tk.history(period="5d")
                if hist.empty:
                    continue
                price   = float(hist["Close"].iloc[-1])
                c_yield = float((tk.info or {}).get("dividendYield", 0) or 0) * 100
            except Exception as e:
                log.error("Exit check failed for %s: %s", ticker, e)
                continue

            if self.risk.check_stop_loss(pos, price):
                self._full_exit(ticker, pos, price, "stop_loss", positions)
            elif self.risk.check_take_profit(pos, price):
                self._partial_exit(ticker, pos, price, "take_profit", positions)
            elif c_yield > 0 and self.risk.check_yield_crisis(pos, c_yield):
                log.info("Yield crisis %s: entry=%.2f%% now=%.2f%%",
                         ticker, pos.entry_yield_pct, c_yield)
                self._full_exit(ticker, pos, price, "yield_crisis", positions)
            elif self.risk.check_max_hold(pos):
                self._full_exit(ticker, pos, price, "max_hold", positions)

    def _full_exit(self, ticker: str, pos: DKPosition, price: float,
                   reason: str, positions: dict):
        broker_pos = self.broker.get_position(ticker)
        qty = broker_pos["qty"] if broker_pos else pos.notional / pos.entry_price
        self.broker.place_sell_order(ticker, qty, reason)
        self.db.log_trade(DKTradeLog(
            ticker=ticker, action="EXIT", reason=reason, price=price,
            notional=qty * price, score=pos.entry_score,
            timestamp=datetime.now().isoformat(),
        ))
        self.db.delete_position(ticker)
        positions.pop(ticker, None)
        pnl = (price - pos.entry_price) / pos.entry_price * 100
        log.info("EXIT  %-6s @ $%.2f  reason=%s  P&L=%+.1f%%", ticker, price, reason, pnl)

    def _partial_exit(self, ticker: str, pos: DKPosition, price: float,
                      reason: str, positions: dict):
        broker_pos = self.broker.get_position(ticker)
        total_qty  = broker_pos["qty"] if broker_pos else pos.notional / pos.entry_price
        sell_qty   = total_qty / 2
        self.broker.place_sell_order(ticker, sell_qty, reason)

        pos.half_taken = True
        pos.notional   = pos.notional / 2
        self.db.save_position(pos)

        self.db.log_trade(DKTradeLog(
            ticker=ticker, action="PARTIAL_EXIT", reason=reason, price=price,
            notional=sell_qty * price, score=pos.entry_score,
            timestamp=datetime.now().isoformat(),
        ))
        pnl = (price - pos.entry_price) / pos.entry_price * 100
        log.info("PARTIAL %-6s @ $%.2f  reason=%s  P&L=%+.1f%%  (half sold)",
                 ticker, price, reason, pnl)


# ---------------------------------------------------------------------------
# Main bot — orchestrator + scheduler
# ---------------------------------------------------------------------------
class DividendKingBot:
    _ET = pytz.timezone("America/New_York")

    def __init__(self, config: DKConfig, broker=None):
        self.cfg      = config
        self.db       = DKDatabase(config.db_path)
        self.scanner  = DividendKingScanner(config)
        self.risk     = DKRisk(config)
        self.broker   = broker or DKDryRunBroker()
        self.executor = DKExecutor(self.broker, self.db, self.risk, config)
        self.positions: dict[str, DKPosition] = self.db.load_positions()
        log.info("DividendKingBot ready. mode=%s  open_positions=%d",
                 config.mode, len(self.positions))

    # --- Scheduled jobs ---

    def run_scan(self, force: bool = False):
        log.info("=== MORNING SCAN ===")
        df = self.scanner.scan_watchlist()
        if df.empty:
            log.warning("Scan returned no results.")
            return

        log.info("\n%s",
            df[["ticker", "signal", "composite_score", "yield_pct"]].to_string(index=False))

        buy_watch = df[df["signal"].isin(["BUY", "WATCH"])]
        signals = [
            DividendKingSignal(
                ticker=row["ticker"],
                dividend_health_score=row["div_health"],
                trend_template_score=row["trend"],
                momentum_score=row["momentum"],
                value_spread_score=row["value_spread"],
                composite_score=row["composite_score"],
                signal=row["signal"],
                current_price=row["price"],
                current_yield_pct=row["yield_pct"],
                notes=row["notes"],
            )
            for _, row in buy_watch.iterrows()
        ]
        self.executor.execute_entries(signals, self.positions)

        fname = f"dk_scan_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        df.to_csv(fname, index=False)
        log.info("Scan saved → %s", fname)

    def run_exit_check(self):
        log.info("=== EXIT CHECK ===")
        self.positions = self.db.load_positions()
        self.executor.execute_exits(self.positions)

    def run_daily_summary(self):
        self.positions = self.db.load_positions()
        equity = self.broker.get_account_equity()
        log.info("=== DAILY SUMMARY  equity=$%.2f  positions=%d ===",
                 equity, len(self.positions))
        for ticker, pos in self.positions.items():
            try:
                price = float(yf.Ticker(ticker).history(period="1d")["Close"].iloc[-1])
                pnl   = (price - pos.entry_price) / pos.entry_price * 100
                log.info("  %-6s entry=$%.2f  now=$%.2f  P&L=%+.1f%%  yield=%.2f%%",
                         ticker, pos.entry_price, price, pnl, pos.entry_yield_pct)
            except Exception:
                pass

    def print_status(self):
        self.positions = self.db.load_positions()
        equity = self.broker.get_account_equity()
        sep = "=" * 62

        print(f"\n{sep}")
        print("  Dividend King Swing Trading Bot — Portfolio Status")
        print(sep)
        print(f"  Account equity : ${equity:>12,.2f}")
        print(f"  Open positions : {len(self.positions)}")
        print(f"  Mode           : {self.cfg.mode.upper()}")
        print(f"  {'Ticker':<8} {'Entry Date':<12} {'Entry $':>8} {'Yield':>7} "
              f"{'Days':>6} {'Half':>5}")
        print(f"  {'-'*60}")
        for ticker, pos in self.positions.items():
            days = (datetime.now() - datetime.strptime(pos.entry_date, "%Y-%m-%d")).days
            print(f"  {ticker:<8} {pos.entry_date:<12} ${pos.entry_price:>7.2f} "
                  f"{pos.entry_yield_pct:>6.2f}% {days:>5}d "
                  f"{'Yes' if pos.half_taken else 'No':>5}")

        print(f"\n  {'Recent Trades':}")
        print(f"  {'-'*60}")
        for t in self.db.get_trade_history(10):
            print(f"  {t.timestamp[:10]}  {t.action:<14}  {t.ticker:<6}  "
                  f"${t.price:<8.2f}  {t.reason}")
        print()

    # --- Scheduler loop ---

    def run(self):
        log.info("Scheduler started (ET). Ctrl+C to stop.")
        fired_today: set[str] = set()
        last_date: Optional[str] = None

        while True:
            now   = datetime.now(self._ET)
            today = now.strftime("%Y-%m-%d")
            hhmm  = now.strftime("%H:%M")

            if today != last_date:
                fired_today.clear()
                last_date = today

            if now.weekday() < 5:  # Mon–Fri
                self._maybe_fire(f"scan_{today}",    hhmm, self.cfg.scan_time,
                                 fired_today, self.run_scan)
                for t in self.cfg.exit_check_times:
                    self._maybe_fire(f"exit_{today}_{t}", hhmm, t,
                                     fired_today, self.run_exit_check)
                self._maybe_fire(f"summary_{today}", hhmm, self.cfg.summary_time,
                                 fired_today, self.run_daily_summary)

            time.sleep(30)

    def _maybe_fire(self, key: str, current: str, target: str,
                    fired: set, fn):
        if current == target and key not in fired:
            fired.add(key)
            try:
                fn()
            except Exception as e:
                log.error("Job %s failed: %s", key, e)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_bot(mode: str, config: DKConfig) -> DividendKingBot:
    config.mode = mode
    if mode == "dry":
        broker = DKDryRunBroker()
    elif mode == "paper":
        broker = DKAlpacaBroker(config.alpaca_api_key, config.alpaca_secret_key, live=False)
    elif mode == "live":
        if os.getenv("DK_LIVE_CONFIRM") != "YES_USE_REAL_MONEY":
            raise ValueError(
                "Set DK_LIVE_CONFIRM=YES_USE_REAL_MONEY to enable live trading."
            )
        broker = DKAlpacaBroker(config.alpaca_api_key, config.alpaca_secret_key, live=True)
    else:
        raise ValueError(f"Unknown mode '{mode}'. Use: dry | paper | live")
    return DividendKingBot(config, broker)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Dividend King Swing Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 dividend_king_bot.py                       dry run (scheduler)
  python3 dividend_king_bot.py --mode paper          Alpaca paper trading
  python3 dividend_king_bot.py --mode live           Alpaca live (requires DK_LIVE_CONFIRM)
  python3 dividend_king_bot.py --scan-now            single scan then exit
  python3 dividend_king_bot.py --exit-check-now      single exit check then exit
  python3 dividend_king_bot.py --status              portfolio summary
  python3 dividend_king_bot.py --ticker PG TGT WMT   score specific tickers
        """,
    )
    parser.add_argument("--mode", choices=["dry", "paper", "live"], default="dry")
    parser.add_argument("--scan-now",        action="store_true")
    parser.add_argument("--exit-check-now",  action="store_true")
    parser.add_argument("--status",          action="store_true")
    parser.add_argument("--ticker",          nargs="+", metavar="TICKER")
    parser.add_argument("--force",           action="store_true",
                        help="Force scan even outside market hours")
    args = parser.parse_args()

    config = DKConfig()
    bot    = build_bot(args.mode, config)

    if args.status:
        bot.print_status()
    elif args.ticker:
        df = bot.scanner.scan_watchlist(args.ticker)
        print(df.to_string(index=False))
    elif args.scan_now:
        bot.run_scan(force=args.force)
    elif args.exit_check_now:
        bot.run_exit_check()
    else:
        bot.run()


if __name__ == "__main__":
    main()

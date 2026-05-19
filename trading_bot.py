"""
QQQ Swing Trading Bot
=====================
Implements 6 strategies:
  1. Momentum Entry     (Minervini / O'Neil)
  2. Darvas Breakout    (Darvas)
  3. VCP Entry          (Minervini)
  4. Pullback Re-Entry  (O'Neil / Williams)
  5. Break & Bounce     (false breakdown reversal)
  6. Touch & Turn       (support rejection)

Data:   yfinance (free, daily bars)
Broker: Alpaca Paper Trading API (set ALPACA_KEY / ALPACA_SECRET env vars)
        — remove the alpaca block and swap in any broker SDK as needed
"""

import os
import json
import logging
import smtplib
import textwrap
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import numpy as np
import pandas as pd
import pytz
import yfinance as yf

# ── Optional: Alpaca execution ────────────────────────────────────────────────
try:
    import alpaca_trade_api as tradeapi
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("qqq_bot")

# ─────────────────────────────────────────────────────────────────────────────
# SCAN CACHE  — pre-market results persisted to disk for intraday reuse
# ─────────────────────────────────────────────────────────────────────────────
SCAN_CACHE_PATH = "/tmp/qqq_scan_cache.json"


def _save_scan_cache(results: list) -> None:
    """Serialise ScanResult list to JSON so the 15-min monitor can reuse it."""
    try:
        data = [{
            "ticker":    r.ticker,
            "score":     r.score,
            "price":     r.price,
            "stop":      r.stop,
            "target":    r.target,
            "tt_score":  r.tt_score,
            "stage":     r.stage,
            "rsi":       r.rsi,
            "signals":   [s.strategy for s in r.signals],
            "vol_surge": r.vol_surge,
            "ts":        datetime.now().isoformat(),
        } for r in results]
        with open(SCAN_CACHE_PATH, "w") as fh:
            json.dump(data, fh)
        log.info(f"Scan cache saved ({len(data)} results) → {SCAN_CACHE_PATH}")
    except Exception as e:
        log.warning(f"Could not save scan cache: {e}")


class _CachedSetup:
    """Lightweight stand-in for ScanResult built from cached JSON data."""
    __slots__ = ("ticker","score","price","stop","target","tt_score",
                 "stage","rsi","vol_surge","_sigs","ts")

    def __init__(self, d: dict):
        self.ticker    = d["ticker"]
        self.score     = d["score"]
        self.price     = d["price"]
        self.stop      = d["stop"]
        self.target    = d["target"]
        self.tt_score  = d["tt_score"]
        self.stage     = d["stage"]
        self.rsi       = d["rsi"]
        self.vol_surge = d["vol_surge"]
        self._sigs     = d.get("signals", [])
        self.ts        = d.get("ts", "")

    def signal_names(self) -> str:
        return " · ".join(self._sigs)

    def rr(self) -> float:
        risk   = abs(self.price - self.stop)
        reward = abs(self.target - self.price)
        return round(reward / risk, 2) if risk > 0 else 0.0


def _load_scan_cache() -> list[_CachedSetup]:
    """Load cached scan results. Returns empty list if cache is missing/stale."""
    try:
        with open(SCAN_CACHE_PATH) as fh:
            data = json.load(fh)
        if not data:
            return []
        age_mins = (datetime.now() - datetime.fromisoformat(data[0]["ts"])).seconds // 60
        log.info(f"Scan cache loaded: {len(data)} results, age={age_mins} min")
        # Discard cache older than 10 hours (stale after market close)
        if age_mins > 600:
            log.info("  Cache too old — ignoring.")
            return []
        return [_CachedSetup(d) for d in data]
    except FileNotFoundError:
        log.info("No scan cache found.")
        return []
    except Exception as e:
        log.warning(f"Could not load scan cache: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Config:
    symbol:           str   = "QQQ"
    lookback_days:    int   = 300        # bars to fetch
    vix_symbol:       str   = "^VIX"

    # Moving averages
    ema_fast:         int   = 21
    ema_slow:         int   = 50
    sma_mid:          int   = 150
    sma_long:         int   = 200

    # Trend Template
    tt_min_score:     int   = 6          # min score for full entry
    tt_pullbk_score:  int   = 7          # score required for pullback add

    # Darvas
    darvas_len:       int   = 20

    # VCP
    vcp_len:          int   = 10
    vcp_atr_ratio:    float = 0.70       # ATR must contract to 70% of prior

    # Oscillators
    rsi_len:          int   = 14
    rsi_ob:           int   = 70
    rsi_os:           int   = 40
    macd_fast:        int   = 12
    macd_slow:        int   = 26
    macd_sig:         int   = 9

    # Volume
    vol_ma_len:       int   = 20
    vol_surge_mult:   float = 1.5        # surge = volume > MA * mult
    vol_dry_mult:     float = 0.75       # dry = volume < MA * mult

    # ATR / Risk
    atr_len:          int   = 14
    atr_sl_mult:      float = 2.0
    atr_tp_mult:      float = 3.0

    # Break & Bounce
    bb_require_vol:   bool  = True

    # Touch & Turn
    touch_tol_pct:    float = 0.4        # wick must be within 0.4% of level

    # Email
    email_to:         str   = "kory.lernout@me.com"
    email_from:       str   = field(default_factory=lambda: os.getenv("EMAIL_FROM", ""))
    smtp_host:        str   = "smtp.gmail.com"
    smtp_port:        int   = 587
    smtp_user:        str   = field(default_factory=lambda: os.getenv("EMAIL_FROM", ""))
    smtp_pass:        str   = field(default_factory=lambda: os.getenv("EMAIL_PASS", ""))

    # Strategies to enable
    en_momentum:      bool  = True
    en_darvas:        bool  = True
    en_vcp:           bool  = True
    en_pullback:      bool  = True
    en_break_bounce:  bool  = True
    en_touch_turn:    bool  = True

    # Execution
    paper_trading:    bool  = True
    max_position_pct: float = 0.15       # 15% of portfolio per trade
    alpaca_key:       str   = field(default_factory=lambda: os.getenv("ALPACA_KEY", ""))
    alpaca_secret:    str   = field(default_factory=lambda: os.getenv("ALPACA_SECRET", ""))
    alpaca_base_url:  str   = "https://paper-api.alpaca.markets"


# ─────────────────────────────────────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────────────────────────────────────
class Indicators:
    """Compute all technical indicators from a OHLCV DataFrame."""

    def __init__(self, df: pd.DataFrame, cfg: Config):
        self.df  = df.copy()
        self.cfg = cfg
        self._compute()

    # ── helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False).mean()

    @staticmethod
    def _sma(series: pd.Series, window: int) -> pd.Series:
        return series.rolling(window).mean()

    @staticmethod
    def _atr(df: pd.DataFrame, length: int) -> pd.Series:
        h, l, c = df["High"], df["Low"], df["Close"]
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        return tr.ewm(span=length, adjust=False).mean()

    @staticmethod
    def _rsi(series: pd.Series, length: int) -> pd.Series:
        delta = series.diff()
        gain  = delta.clip(lower=0).ewm(com=length - 1, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(com=length - 1, adjust=False).mean()
        rs    = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _macd(series: pd.Series, fast: int, slow: int, sig: int):
        ema_f   = series.ewm(span=fast, adjust=False).mean()
        ema_s   = series.ewm(span=slow, adjust=False).mean()
        macd    = ema_f - ema_s
        signal  = macd.ewm(span=sig, adjust=False).mean()
        hist    = macd - signal
        return macd, signal, hist

    # ── main computation ──────────────────────────────────────────────────────
    def _compute(self):
        df  = self.df
        cfg = self.cfg
        c   = df["Close"]
        h   = df["High"]
        l   = df["Low"]
        v   = df["Volume"]

        # Moving averages
        df["ema21"]  = self._ema(c, cfg.ema_fast)
        df["ema50"]  = self._ema(c, cfg.ema_slow)
        df["sma150"] = self._sma(c, cfg.sma_mid)
        df["sma200"] = self._sma(c, cfg.sma_long)

        # Trend Template
        df["tt1"] = c > df["sma200"]
        df["tt2"] = df["sma200"] > df["sma200"].shift(21)
        df["tt3"] = df["sma150"] > df["sma200"]
        df["tt4"] = (df["ema50"] > df["sma150"]) & (df["ema50"] > df["sma200"])
        df["tt5"] = c > df["ema50"]
        df["tt6"] = c >= l.rolling(252).min() * 1.30
        df["tt7"] = c >= h.rolling(252).max() * 0.75
        df["tt_score"] = df[["tt1","tt2","tt3","tt4","tt5","tt6","tt7"]].sum(axis=1).astype(int)

        # Stage Analysis
        df["slope"]  = df["sma200"] - df["sma200"].shift(10)
        df["stage2"] = (c > df["sma200"]) & (df["slope"] > 0)
        df["stage4"] = (c < df["sma200"]) & (df["slope"] < 0)

        # Darvas Box
        df["d_top"] = h.rolling(cfg.darvas_len).max()
        df["d_bot"] = l.rolling(cfg.darvas_len).min()
        df["darvas_bo"] = c > df["d_top"].shift(1)

        # ATR & VCP
        df["atr"]    = self._atr(df, cfg.atr_len)
        df["vcp_ok"] = df["atr"] < df["atr"].shift(cfg.vcp_len) * cfg.vcp_atr_ratio

        # Oscillators
        df["rsi"]          = self._rsi(c, cfg.rsi_len)
        df["rsi_momentum"] = (df["rsi"] > 55) & (df["rsi"] < cfg.rsi_ob)
        df["rsi_pullback"] = (df["rsi"] >= cfg.rsi_os) & (df["rsi"] < 55)

        macd, sig, hist        = self._macd(c, cfg.macd_fast, cfg.macd_slow, cfg.macd_sig)
        df["macd"]             = macd
        df["macd_sig"]         = sig
        df["macd_hist"]        = hist
        df["macd_bull"]        = (macd > sig) & (macd > 0)
        df["macd_cross"]       = (macd > sig) & (macd.shift(1) <= sig.shift(1))

        # Volume
        df["vol_ma"]    = self._sma(v, cfg.vol_ma_len)
        df["vol_surge"] = v > df["vol_ma"] * cfg.vol_surge_mult
        df["vol_dry"]   = v < df["vol_ma"] * cfg.vol_dry_mult

        # Risk levels (based on last close)
        df["sl"] = c - df["atr"] * cfg.atr_sl_mult
        df["tp"] = c + df["atr"] * cfg.atr_tp_mult

        self.df = df


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL RESULT
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Signal:
    strategy:   str
    direction:  str           # "long" | "exit"
    price:      float
    stop:       float
    target:     float
    atr:        float
    tt_score:   int
    stage:      str
    reason:     str
    timestamp:  datetime = field(default_factory=datetime.now)

    def rr(self) -> float:
        """Risk-to-reward ratio."""
        risk   = abs(self.price - self.stop)
        reward = abs(self.target - self.price)
        return round(reward / risk, 2) if risk > 0 else 0.0

    def __str__(self):
        return (
            f"[{self.strategy}] {self.direction.upper()}  "
            f"@ ${self.price:.2f}  SL=${self.stop:.2f}  TP=${self.target:.2f}  "
            f"R:R={self.rr()}  TT={self.tt_score}/7  Stage={self.stage}  | {self.reason}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGIES
# ─────────────────────────────────────────────────────────────────────────────
class Strategies:
    def __init__(self, ind: Indicators):
        self.df  = ind.df
        self.cfg = ind.cfg

    def _row(self, i: int = -1) -> pd.Series:
        return self.df.iloc[i]

    def _stage(self, r: pd.Series) -> str:
        return "2-Bull" if r["stage2"] else "4-Bear" if r["stage4"] else "1/3-Neutral"

    def _sig(self, strategy: str, r: pd.Series, reason: str) -> Signal:
        return Signal(
            strategy  = strategy,
            direction = "long",
            price     = r["Close"],
            stop      = r["sl"],
            target    = r["tp"],
            atr       = r["atr"],
            tt_score  = int(r["tt_score"]),
            stage     = self._stage(r),
            reason    = reason,
        )

    # ── 1. Momentum Entry ─────────────────────────────────────────────────────
    def momentum_entry(self) -> Optional[Signal]:
        r = self._row()
        if not self.cfg.en_momentum:
            return None
        if (r["tt_score"] >= self.cfg.tt_min_score and r["stage2"]
                and r["macd_bull"] and r["rsi_momentum"] and r["vol_surge"]):
            return self._sig("Momentum", r,
                             f"TT={r['tt_score']}/7 | MACD bull | RSI={r['rsi']:.1f} | Vol surge")
        return None

    # ── 2. Darvas Breakout ────────────────────────────────────────────────────
    def darvas_breakout(self) -> Optional[Signal]:
        r = self._row()
        if not self.cfg.en_darvas:
            return None
        if r["darvas_bo"] and r["tt_score"] >= 5 and r["vol_surge"]:
            return self._sig("Darvas BO", r,
                             f"Break above ${r['d_top']:.2f} box top | Vol surge")
        return None

    # ── 3. VCP Entry ──────────────────────────────────────────────────────────
    def vcp_entry(self) -> Optional[Signal]:
        r = self._row()
        if not self.cfg.en_vcp:
            return None
        if (r["vcp_ok"] and r["tt_score"] >= 5
                and r["macd_cross"] and r["vol_surge"]):
            return self._sig("VCP", r,
                             f"ATR contracted | MACD cross | Vol surge")
        return None

    # ── 4. Pullback Re-Entry ──────────────────────────────────────────────────
    def pullback_entry(self) -> Optional[Signal]:
        r = self._row()
        if not self.cfg.en_pullback:
            return None
        if (r["tt_score"] == self.cfg.tt_pullbk_score and r["stage2"]
                and r["rsi_pullback"] and r["Close"] > r["ema21"]
                and r["macd_bull"] and r["vol_dry"]):
            return self._sig("Pullback", r,
                             f"TT=7/7 | RSI pullback={r['rsi']:.1f} | Low volume | Above EMA21")
        return None

    # ── 5. Break & Bounce ─────────────────────────────────────────────────────
    def break_and_bounce(self) -> Optional[Signal]:
        if not self.cfg.en_break_bounce:
            return None

        curr = self._row(-1)
        prev = self._row(-2)

        if curr["stage4"]:
            return None
        if curr["tt_score"] < 4:
            return None

        bull_candle = curr["Close"] > curr["Open"]
        vol_ok      = curr["vol_surge"] if self.cfg.bb_require_vol else True

        # Check each level for break (prev close below) → bounce (curr close above)
        levels = {
            "EMA 21":     (curr["ema21"],  prev["Close"] < prev["ema21"],  curr["Close"] > curr["ema21"]),
            "EMA 50":     (curr["ema50"],  prev["Close"] < prev["ema50"],  curr["Close"] > curr["ema50"]),
            "Darvas Bot": (curr["d_bot"],  prev["Close"] < prev["d_bot"],  curr["Close"] > curr["d_bot"]),
        }

        for level_name, (level_val, broke, bounced) in levels.items():
            if broke and bounced and bull_candle and vol_ok:
                return Signal(
                    strategy  = "Break & Bounce",
                    direction = "long",
                    price     = curr["Close"],
                    stop      = curr["sl"],
                    target    = curr["tp"],
                    atr       = curr["atr"],
                    tt_score  = int(curr["tt_score"]),
                    stage     = self._stage(curr),
                    reason    = (f"False breakdown below {level_name} "
                                 f"(prev close ${prev['Close']:.2f} < ${level_val:.2f}) "
                                 f"→ bounce close ${curr['Close']:.2f}"),
                )
        return None

    # ── 6. Touch & Turn ───────────────────────────────────────────────────────
    def touch_and_turn(self) -> Optional[Signal]:
        if not self.cfg.en_touch_turn:
            return None

        r   = self._row()
        tol = self.cfg.touch_tol_pct / 100

        if not r["stage2"] or r["tt_score"] < 5:
            return None

        bar_mid     = (r["Low"] + r["High"]) / 2
        close_upper = r["Close"] > bar_mid      # close in upper half = rejection wick

        # Check each level: wick touches within tol%, close above
        levels = {
            "EMA 21":  r["ema21"],
            "EMA 50":  r["ema50"],
            "SMA 200": r["sma200"],
            "Darvas Bot": r["d_bot"],
        }

        for level_name, level_val in levels.items():
            touched = r["Low"] <= level_val * (1 + tol)
            held    = r["Close"] > level_val
            if touched and held and close_upper:
                return Signal(
                    strategy  = "Touch & Turn",
                    direction = "long",
                    price     = r["Close"],
                    stop      = r["sl"],
                    target    = r["tp"],
                    atr       = r["atr"],
                    tt_score  = int(r["tt_score"]),
                    stage     = self._stage(r),
                    reason    = (f"Wick touched {level_name} (${level_val:.2f}) "
                                 f"low=${r['Low']:.2f}, close=${r['Close']:.2f} — rejection confirmed"),
                )
        return None

    # ── Exit ──────────────────────────────────────────────────────────────────
    def exit_signal(self) -> Optional[Signal]:
        r    = self._row()
        prev = self._row(-2)
        ema50_break = prev["Close"] > prev["ema50"] and r["Close"] < r["ema50"] and r["vol_surge"]

        if ema50_break or r["stage4"]:
            reason = "Stage 4 onset" if r["stage4"] else "EMA50 breakdown on volume"
            return Signal(
                strategy  = "Exit",
                direction = "exit",
                price     = r["Close"],
                stop      = r["Close"],
                target    = r["Close"],
                atr       = r["atr"],
                tt_score  = int(r["tt_score"]),
                stage     = self._stage(r),
                reason    = reason,
            )
        return None

    def all_signals(self) -> list[Signal]:
        checks = [
            self.momentum_entry,
            self.darvas_breakout,
            self.vcp_entry,
            self.pullback_entry,
            self.break_and_bounce,
            self.touch_and_turn,
        ]
        return [s for fn in checks if (s := fn()) is not None]


# ─────────────────────────────────────────────────────────────────────────────
# DATA
# ─────────────────────────────────────────────────────────────────────────────
def fetch_data(symbol: str, days: int) -> pd.DataFrame:
    end   = datetime.today()
    start = end - timedelta(days=days)
    log.info(f"Fetching {symbol} from {start.date()} to {end.date()}")
    df = yf.download(symbol, start=start.strftime("%Y-%m-%d"),
                     end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"No data returned for {symbol}")
    df.index = pd.to_datetime(df.index)
    # Flatten multi-level columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    log.info(f"  → {len(df)} bars loaded (last: {df.index[-1].date()})")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# BROKER (Alpaca paper)
# ─────────────────────────────────────────────────────────────────────────────
class AlpacaBroker:
    def __init__(self, cfg: Config):
        if not ALPACA_AVAILABLE:
            raise ImportError("alpaca-trade-api not installed. Run: pip install alpaca-trade-api")
        self.api = tradeapi.REST(cfg.alpaca_key, cfg.alpaca_secret, cfg.alpaca_base_url)
        self.cfg = cfg

    def portfolio_value(self) -> float:
        return float(self.api.get_account().portfolio_value)

    def current_position(self, symbol: str) -> float:
        try:
            return float(self.api.get_position(symbol).qty)
        except Exception:
            return 0.0

    def place_long(self, symbol: str, signal: Signal) -> None:
        value    = self.portfolio_value() * self.cfg.max_position_pct
        qty      = int(value // signal.price)
        if qty < 1:
            log.warning("Position too small to place order.")
            return

        log.info(f"  PLACING ORDER: BUY {qty} {symbol} @ ~${signal.price:.2f}")
        self.api.submit_order(
            symbol        = symbol,
            qty           = qty,
            side          = "buy",
            type          = "market",
            time_in_force = "day",
        )

    def close_position(self, symbol: str) -> None:
        qty = self.current_position(symbol)
        if qty > 0:
            log.info(f"  CLOSING {qty} shares of {symbol}")
            self.api.submit_order(
                symbol=symbol, qty=int(qty), side="sell",
                type="market", time_in_force="day",
            )
        else:
            log.info(f"  No open position in {symbol} to close.")


# ─────────────────────────────────────────────────────────────────────────────
# BOT
# ─────────────────────────────────────────────────────────────────────────────
class TradingBot:
    def __init__(self, cfg: Config = None):
        self.cfg    = cfg or Config()
        self.broker = None
        if ALPACA_AVAILABLE and self.cfg.alpaca_key:
            try:
                self.broker = AlpacaBroker(self.cfg)
                log.info("Alpaca broker connected.")
            except Exception as e:
                log.warning(f"Alpaca connection failed: {e}. Running in signal-only mode.")

    def run(self) -> list[Signal]:
        # ── Fetch & compute ───────────────────────────────────────────────────
        df  = fetch_data(self.cfg.symbol, self.cfg.lookback_days)
        ind = Indicators(df, self.cfg)
        str_engine = Strategies(ind)

        # ── Dashboard snapshot ────────────────────────────────────────────────
        r = ind.df.iloc[-1]
        log.info("─" * 60)
        log.info(f"  {self.cfg.symbol}  |  {df.index[-1].date()}  |  Close: ${r['Close']:.2f}")
        log.info(f"  Trend Template : {int(r['tt_score'])}/7")
        stage_str = "Stage 2 Bull" if r["stage2"] else "Stage 4 Bear" if r["stage4"] else "Stage 1/3 Neutral"
        log.info(f"  Stage          : {stage_str}")
        log.info(f"  MACD Bull      : {bool(r['macd_bull'])}")
        log.info(f"  RSI            : {r['rsi']:.1f}")
        log.info(f"  Vol Surge      : {bool(r['vol_surge'])}")
        log.info(f"  VCP Tight      : {bool(r['vcp_ok'])}")
        log.info(f"  Stop (2×ATR)   : ${r['sl']:.2f}  |  Target (3×ATR): ${r['tp']:.2f}")
        log.info("─" * 60)

        # ── Check exit first ──────────────────────────────────────────────────
        exit_sig = str_engine.exit_signal()
        if exit_sig:
            log.info(f"EXIT SIGNAL  →  {exit_sig}")
            if self.broker:
                self.broker.close_position(self.cfg.symbol)
            return [exit_sig]

        # ── Entry signals ─────────────────────────────────────────────────────
        signals = str_engine.all_signals()

        if not signals:
            log.info("No entry signals today.")
            return []

        log.info(f"{len(signals)} signal(s) found:")
        for sig in signals:
            log.info(f"  ✦  {sig}")

        # ── Execute best signal (highest TT score, break ties by R:R) ─────────
        if self.broker:
            position = self.broker.current_position(self.cfg.symbol)
            if position == 0:
                best = max(signals, key=lambda s: (s.tt_score, s.rr()))
                log.info(f"  → Executing: {best.strategy}")
                self.broker.place_long(self.cfg.symbol, best)
            else:
                log.info(f"  → Already holding {position} shares. No new order.")

        return signals


# ─────────────────────────────────────────────────────────────────────────────
# EMAILER
# ─────────────────────────────────────────────────────────────────────────────
class Emailer:
    """Send HTML emails via iCloud SMTP (smtp.mail.me.com:587).
    Requires an Apple app-specific password — generate at:
    appleid.apple.com → Sign-In & Security → App-Specific Passwords
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def send(self, subject: str, html: str) -> bool:
        if not all([self.cfg.smtp_user, self.cfg.smtp_pass, self.cfg.email_from]):
            log.warning("Email credentials not set — skipping send. "
                        "Set EMAIL_FROM, EMAIL_USER, EMAIL_PASS env vars.")
            return False
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = self.cfg.email_from
            msg["To"]      = self.cfg.email_to
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(self.cfg.smtp_user, self.cfg.smtp_pass)
                server.sendmail(self.cfg.email_from, self.cfg.email_to, msg.as_string())

            log.info(f"Email sent → {self.cfg.email_to}  [{subject}]")
            return True
        except Exception as e:
            log.error(f"Email failed: {e}")
            return False


# ─────────────────────────────────────────────────────────────────────────────
# REPORT BUILDER
# ─────────────────────────────────────────────────────────────────────────────
_CSS = """
  body { font-family: -apple-system, Arial, sans-serif; background:#0d0d0d; color:#e0e0e0; margin:0; padding:20px; }
  .card { background:#1a1a1a; border-radius:10px; padding:20px; max-width:600px; margin:0 auto; }
  h2 { color:#00E5FF; margin-top:0; font-size:18px; letter-spacing:1px; }
  h3 { color:#FF9800; font-size:14px; margin:18px 0 8px; text-transform:uppercase; letter-spacing:1px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  td { padding:6px 10px; border-bottom:1px solid #2a2a2a; }
  td:first-child { color:#9e9e9e; width:45%; }
  td:last-child { font-weight:600; }
  .ok  { color:#66BB6A; }
  .no  { color:#EF5350; }
  .warn{ color:#FFA726; }
  .neu { color:#90A4AE; }
  .sig { background:#1b3a1b; border-left:3px solid #66BB6A; padding:10px; border-radius:4px; margin:6px 0; font-size:13px; }
  .exit{ background:#3a1b1b; border-left:3px solid #EF5350; padding:10px; border-radius:4px; margin:6px 0; font-size:13px; }
  .rule{ background:#1a2a3a; border-left:3px solid #00E5FF; padding:10px; border-radius:4px; margin:6px 0; font-size:12px; color:#b0bec5; }
  .foot{ font-size:11px; color:#555; text-align:center; margin-top:16px; }
"""

def _val_class(val, good, bad=None) -> str:
    if val == good:   return "ok"
    if val == bad:    return "no"
    return "warn"

def _score_class(score: int) -> str:
    return "ok" if score >= 6 else "warn" if score >= 4 else "no"

def _vix_class(vix: float) -> str:
    return "ok" if vix < 20 else "warn" if vix < 30 else "no"

def _vix_rule(vix: float) -> str:
    if vix > 40: return "⛔  VIX > 40 — CASH ONLY. No new trades."
    if vix > 30: return "⚠️  VIX > 30 — Cut all position sizes 50%."
    if vix > 20: return "⚠️  VIX 20–30 — Reduce new position sizes 50%."
    return "✅  VIX < 20 — Full position sizes allowed."


# ─────────────────────────────────────────────────────────────────────────────
# PRE-MARKET SCANNER
# ─────────────────────────────────────────────────────────────────────────────

# Personal focus tickers — always shown in every email with full signal status
FOCUS_TICKERS = ["NVDA", "AMZN", "TSLA", "SU.TO", "GME", "AMC"]

# Nasdaq-100 + high-cap S&P watchlist (covers the QQQ heat map universe)
_WATCHLIST = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","AVGO","COST","NFLX",
    "AMD","QCOM","INTC","INTU","ADBE","TXN","AMAT","MU","KLAC","LRCX",
    "MRVL","ON","SMCI","ARM","CRWD","PANW","SNPS","CDNS","FTNT","TEAM",
    "ORCL","CRM","NOW","WDAY","ZS","DDOG","MDB","SNOW","NET","OKTA",
    "SHOP","MELI","PDD","JD","BIDU","BABA","TSM","ASML","SAP","ERIC",
    "COIN","HOOD","SOFI","PYPL","V","MA","AXP","JPM","GS","MS",
    "UBER","LYFT","ABNB","DASH","RBLX","SPOT","SNAP","PINS","RDDT","APP",
    "LLY","UNH","ABBV","JNJ","MRK","PFE","BMY","GILD","AMGN","REGN",
    "XOM","CVX","COP","OXY","SLB","HAL","MPC","VLO","PSX","EOG",
    "SPY","QQQ","IWM","GLD","SLV","TLT","USO","UNG","ARKK","SOXS",
    "GME","AMC","SU.TO",
]

@dataclass
class PreMarketMover:
    ticker:     str
    price:      float
    prev_close: float
    pct_chg:    float
    pm_volume:  int
    direction:  str   # "up" | "down"

    def cls(self) -> str:
        return "ok" if self.direction == "up" else "no"


class PreMarketScanner:
    """Scan the watchlist for the top 3 pre-market movers by |% change| × volume."""

    def __init__(self, watchlist: list[str] = None, top_n: int = 3):
        self.watchlist = watchlist or _WATCHLIST
        self.top_n     = top_n

    def scan(self) -> list[PreMarketMover]:
        log.info(f"Pre-market scan: {len(self.watchlist)} tickers...")
        results: list[PreMarketMover] = []

        try:
            # Download 5-day 1-minute bars with pre/post market included
            raw = yf.download(
                tickers    = " ".join(self.watchlist),
                period     = "5d",
                interval   = "1m",
                prepost    = True,
                progress   = False,
                auto_adjust= True,
                group_by   = "ticker",
            )
        except Exception as e:
            log.warning(f"Pre-market scan failed: {e}")
            return []

        now_et = datetime.now(pytz.timezone("America/New_York"))
        today  = now_et.date()

        for ticker in self.watchlist:
            try:
                # Extract this ticker's slice
                if isinstance(raw.columns, pd.MultiIndex):
                    df = raw[ticker].dropna(how="all")
                else:
                    df = raw.dropna(how="all")

                if df.empty:
                    continue

                df.index = pd.to_datetime(df.index).tz_convert("America/New_York")

                # Previous regular-session close (last 4pm bar)
                prev = df[
                    (df.index.date < today) &
                    (df.index.hour >= 9) & (df.index.hour < 16)
                ]
                if prev.empty:
                    continue
                prev_close = float(prev["Close"].dropna().iloc[-1])

                # Today's pre-market bars (4:00–9:30 AM ET)
                pm = df[
                    (df.index.date == today) &
                    (df.index.hour >= 4) &
                    ~((df.index.hour == 9) & (df.index.minute >= 30))
                ]
                if pm.empty:
                    continue

                pm_price  = float(pm["Close"].dropna().iloc[-1])
                pm_vol    = int(pm["Volume"].sum())
                pct       = ((pm_price - prev_close) / prev_close) * 100

                results.append(PreMarketMover(
                    ticker     = ticker,
                    price      = pm_price,
                    prev_close = prev_close,
                    pct_chg    = pct,
                    pm_volume  = pm_vol,
                    direction  = "up" if pct >= 0 else "down",
                ))
            except Exception:
                continue

        if not results:
            log.info("  No pre-market data found (market may not have opened yet).")
            return []

        return self._rank(results)

    def intraday_scan(self) -> list[PreMarketMover]:
        """Scan regular-session bars for the top bullish movers so far today."""
        log.info(f"Intraday scan: {len(self.watchlist)} tickers...")
        results: list[PreMarketMover] = []

        try:
            raw = yf.download(
                tickers    = " ".join(self.watchlist),
                period     = "2d",
                interval   = "5m",
                prepost    = False,
                progress   = False,
                auto_adjust= True,
                group_by   = "ticker",
            )
        except Exception as e:
            log.warning(f"Intraday scan failed: {e}")
            return []

        now_et = datetime.now(pytz.timezone("America/New_York"))
        today  = now_et.date()

        for ticker in self.watchlist:
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    df = raw[ticker].dropna(how="all")
                else:
                    df = raw.dropna(how="all")
                if df.empty:
                    continue

                df.index = pd.to_datetime(df.index).tz_convert("America/New_York")

                prev = df[df.index.date < today]
                if prev.empty:
                    continue
                prev_close = float(prev["Close"].dropna().iloc[-1])

                today_bars = df[df.index.date == today]
                if today_bars.empty:
                    continue

                cur_price = float(today_bars["Close"].dropna().iloc[-1])
                vol       = int(today_bars["Volume"].sum())
                pct       = ((cur_price - prev_close) / prev_close) * 100

                results.append(PreMarketMover(
                    ticker     = ticker,
                    price      = cur_price,
                    prev_close = prev_close,
                    pct_chg    = pct,
                    pm_volume  = vol,
                    direction  = "up" if pct >= 0 else "down",
                ))
            except Exception:
                continue

        return self._rank(results)

    def _rank(self, results: list[PreMarketMover]) -> list[PreMarketMover]:
        """Return top_n bullish movers sorted by % change × √volume."""
        bullish = [m for m in results if m.direction == "up"]
        bullish.sort(key=lambda m: m.pct_chg * (1 + m.pm_volume ** 0.5), reverse=True)
        top = bullish[:self.top_n]
        for m in top:
            log.info(f"  {m.ticker:6s}  {m.pct_chg:+.2f}%  vol={m.pm_volume:,}")
        return top


# ─────────────────────────────────────────────────────────────────────────────
# MARKET SCANNER  — full S&P 500 + Nasdaq 100 universe
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ScanResult:
    ticker:   str
    score:    float
    price:    float
    stop:     float
    target:   float
    tt_score: int
    stage:    str
    rsi:      float
    signals:  list
    vol_surge:bool

    def signal_names(self) -> str:
        return " · ".join(s.strategy for s in self.signals)

    def rr(self) -> float:
        risk   = abs(self.price - self.stop)
        reward = abs(self.target - self.price)
        return round(reward / risk, 2) if risk > 0 else 0.0


class MarketScanner:
    """
    Fetches S&P 500 + Nasdaq 100 tickers from Wikipedia, downloads 1 year of
    daily bars in one batch, runs the full 6-strategy signal suite on every
    ticker, scores each hit, and returns the top N ranked setups.

    Scoring:
      TT score × 2  (max 14)  — trend quality
      signals × 3   (max ~18) — number of strategies firing
      Stage 2 bonus +2        — confirmed uptrend
      Vol surge     +1        — institutional volume
      MACD bull     +1        — momentum confirmed
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg

    # ── universe ──────────────────────────────────────────────────────────────
    # S&P 500 + Nasdaq 100 embedded — avoids scraping fragility
    _SP500 = [
        "A","AAL","AAP","AAPL","ABBV","ABT","ACGL","ACN","ADBE","ADI","ADM","ADP","ADSK",
        "AEE","AEP","AES","AFL","AIG","AIZ","AJG","AKAM","ALB","ALGN","ALL","ALLE","AMAT",
        "AMCR","AMD","AME","AMGN","AMP","AMT","AMZN","ANET","AON","AOS","APA","APD",
        "APH","APTV","ARE","ATO","AVB","AVGO","AVY","AWK","AXON","AXP","AZO",
        "BA","BAC","BALL","BAX","BBWI","BBY","BDX","BEN","BG","BIIB","BIO","BK",
        "BKNG","BKR","BLK","BMY","BR","BRK-B","BRO","BSX","BX",
        "C","CAG","CAH","CARR","CAT","CB","CBOE","CBRE","CCI","CCL","CDNS","CDW","CE",
        "CEG","CF","CFG","CHD","CHRW","CHTR","CI","CINF","CL","CLX","CMCSA","CME",
        "CMG","CMI","CMS","CNC","CNP","COF","COO","COP","COST","CPB","CPRT","CPT","CRL",
        "CRM","CSCO","CSGP","CSX","CTAS","CTRA","CTSH","CTVA","CVS","CVX","CZR",
        "D","DAL","DD","DE","DECK","DG","DGX","DHI","DHR","DIS","DLR","DLTR",
        "DOC","DOV","DOW","DPZ","DRI","DTE","DUK","DVA","DVN","DXC",
        "EA","EBAY","ECL","ED","EFX","EG","EIX","EL","ELV","EMN","EMR","ENPH","EOG",
        "EPAM","EQIX","EQR","EQT","ES","ESS","ETN","ETR","ETSY","EVRG","EW","EXC","EXR",
        "F","FANG","FAST","FCX","FDS","FDX","FE","FFIV","FICO","FIS","FITB",
        "FMC","FOX","FOXA","FRT","FSLR","FTNT",
        "GD","GE","GEHC","GEN","GILD","GIS","GL","GLW","GM","GNRC","GOOGL","GOOG","GPC",
        "GPN","GRMN","GS","GWW",
        "HAL","HAS","HBAN","HCA","HD","HIG","HII","HLT","HOLX","HON","HPE","HPQ",
        "HRL","HSIC","HST","HSY","HUM","HWM",
        "IBM","ICE","IDXX","IEX","IFF","ILMN","INCY","INTC","INTU","INVH","IP",
        "IQV","IR","IRM","ISRG","IT","ITW","IVZ",
        "J","JBHT","JCI","JKHY","JNJ","JPM",
        "KDP","KEY","KEYS","KHC","KIM","KLAC","KMB","KMI","KMX","KO","KR",
        "L","LDOS","LEN","LH","LHX","LIN","LKQ","LLY","LMT","LNT","LOW","LRCX","LUV",
        "LVS","LW","LYB","LYV",
        "MA","MAA","MAR","MAS","MCD","MCHP","MCK","MCO","MDLZ","MDT","MET","META","MGM",
        "MHK","MKC","MKTX","MLM","MMM","MNST","MO","MOH","MOS","MPC","MPWR","MRK",
        "MRNA","MS","MSCI","MSFT","MSI","MTB","MTD","MU","NCLH",
        "NDAQ","NEE","NEM","NFLX","NI","NKE","NOC","NOW","NRG","NSC","NTAP","NTRS","NUE",
        "NVDA","NVR","NWS","NWSA",
        "O","ODFL","OKE","OMC","ON","ORCL","ORLY","OXY",
        "PANW","PAYC","PAYX","PCAR","PCG","PEG","PEP","PFE","PFG","PG","PGR",
        "PH","PHM","PKG","PLD","PM","PNC","PNR","PNW","PODD","POOL","PPG","PPL","PRU",
        "PSA","PSX","PTC","PWR",
        "QCOM","QRVO",
        "RCL","REG","REGN","RF","RJF","RL","RMD","ROK","ROL","ROP","ROST","RSG","RTX",
        "SBAC","SBUX","SCHW","SEE","SHW","SJM","SLB","SMCI","SNA","SNPS","SO","SPG",
        "SPGI","SRE","STE","STLD","STT","STX","STZ","SWK","SWKS","SYF","SYK","SYY",
        "T","TAP","TDG","TDY","TECH","TEL","TER","TFC","TFX","TGT","TJX","TMO","TMUS",
        "TPR","TRGP","TRMB","TROW","TRV","TSCO","TSLA","TSN","TT","TTWO","TXN","TYL",
        "UAL","UDR","UHS","ULTA","UNH","UNP","UPS","URI","USB",
        "V","VFC","VICI","VLO","VMC","VRSK","VRSN","VRTX","VTR","VTRS",
        "WAB","WAT","WBD","WDC","WELL","WFC","WHR","WM","WMB","WMT","WRB","WST",
        "WTW","WY","WYNN",
        "XEL","XOM","XRAY","XYL",
        "YUM",
        "ZBH","ZBRA","ZTS",
        # Nasdaq 100 extras not in S&P 500
        "ABNB","ADSK","AEP","ALGN","ARM","ASML","BIIB","BKNG","CCEP","CDNS",
        "CEG","CHTR","CMCSA","COST","CPRT","CRWD","CSCO","CSGP","CSX","DDOG","DLTR",
        "DXCM","EA","EXC","FANG","FAST","FTNT","GFS","GILD","HON","IDXX","ILMN","INTC",
        "INTU","ISRG","KDP","KLAC","LRCX","LULU","MAR","MCHP","MDLZ","MELI","META",
        "MNST","MRNA","MRVL","MSFT","MU","NFLX","NVDA","NXPI","ODFL","ON","ORLY","PANW",
        "PAYX","PCAR","PDD","PEP","PYPL","QCOM","REGN","ROST","SBUX","SIRI","SNPS",
        "TEAM","TMUS","TSLA","TTWO","TXN","VRSK","VRTX","WBD","XEL",
        # US focus / high-momentum extras
        "GME","AMC","SHOP","COIN","HOOD","SOFI","RDDT","APP",
    ]

    # TSX 60 + major Canadian stocks (yfinance uses .TO suffix)
    _TSX = [
        # Big 6 Banks
        "RY.TO","TD.TO","BNS.TO","BMO.TO","CM.TO","NA.TO",
        # Insurance / Financials
        "MFC.TO","SLF.TO","GWO.TO","IAG.TO","IFC.TO","POW.TO","FFH.TO",
        # Energy — oil sands, pipelines, E&P
        "SU.TO","CNQ.TO","CVE.TO","IMO.TO","ENB.TO","TRP.TO","PPL.TO",
        "ARX.TO","PEY.TO","TVE.TO","BTE.TO","WCP.TO",
        # Mining / Materials
        "ABX.TO","WPM.TO","FNV.TO","AEM.TO","AGI.TO","FM.TO","CCO.TO",
        "LUN.TO","CS.TO","HBM.TO","OGC.TO","ELD.TO","OR.TO",
        # Telecoms
        "BCE.TO","T.TO","RCI-B.TO",
        # Railways
        "CNR.TO","CP.TO",
        # Retail / Consumer
        "ATD.TO","DOL.TO","L.TO","MRU.TO","WN.TO","EMP-A.TO",
        # Technology
        "CSU.TO","OTEX.TO","KXS.TO","DSG.TO","LSPD.TO",
        # Industrials / Infrastructure
        "WSP.TO","STN.TO","TIH.TO","CAE.TO","AC.TO","BBD-B.TO",
        # Utilities
        "FTS.TO","AQN.TO","EMA.TO","H.TO","BEP-UN.TO","BEPC.TO",
        # Real Estate / Diversified
        "BAM.TO","BN.TO","BIP-UN.TO","BIPC.TO","WCN.TO","TRI.TO","QSR.TO",
        # REITs
        "REI-UN.TO","AP-UN.TO","DIR-UN.TO","CRT-UN.TO","SRU-UN.TO",
    ]

    @staticmethod
    def _get_universe() -> list[str]:
        # Merge S&P 500 / Nasdaq 100 + TSX, deduplicate, return
        combined = MarketScanner._SP500 + MarketScanner._TSX
        return list(dict.fromkeys(combined))

    # ── scan ──────────────────────────────────────────────────────────────────
    def scan(self, top_n: int = 5) -> list[ScanResult]:
        universe = self._get_universe()
        log.info(f"Market scan: downloading {len(universe)} tickers (1y daily)…")

        try:
            raw = yf.download(
                tickers     = " ".join(universe),
                period      = "1y",
                interval    = "1d",
                progress    = False,
                auto_adjust = True,
                group_by    = "ticker",
            )
        except Exception as e:
            log.error(f"Batch download failed: {e}")
            return []

        log.info("  Download complete. Running signal suite…")
        results: list[ScanResult] = []

        for ticker in universe:
            try:
                df = raw[ticker].dropna(how="all") if isinstance(raw.columns, pd.MultiIndex) else raw.dropna(how="all")
                if len(df) < 60:
                    continue

                ind    = Indicators(df, self.cfg)
                strat  = Strategies(ind)
                sigs   = strat.all_signals()
                exit_s = strat.exit_signal()

                # Skip if exit firing or no entry signals
                if exit_s or not sigs:
                    continue

                r     = ind.df.iloc[-1]
                stage = "2-Bull" if r["stage2"] else "4-Bear" if r["stage4"] else "1/3"

                score = (
                    int(r["tt_score"]) * 2 +
                    len(sigs) * 3 +
                    (2 if r["stage2"]    else 0) +
                    (1 if r["vol_surge"] else 0) +
                    (1 if r["macd_bull"] else 0)
                )

                results.append(ScanResult(
                    ticker    = ticker,
                    score     = score,
                    price     = float(r["Close"]),
                    stop      = float(r["sl"]),
                    target    = float(r["tp"]),
                    tt_score  = int(r["tt_score"]),
                    stage     = stage,
                    rsi       = float(r["rsi"]),
                    signals   = sigs,
                    vol_surge = bool(r["vol_surge"]),
                ))
            except Exception:
                continue

        results.sort(key=lambda x: x.score, reverse=True)
        top = results[:top_n]
        log.info(f"  {len(results)} setups found. Top {top_n}:")
        for r in top:
            log.info(f"    {r.ticker:6s}  score={r.score}  TT={r.tt_score}/7  {r.signal_names()}")
        _save_scan_cache(top)
        return top


# ─────────────────────────────────────────────────────────────────────────────
# TRADE EXECUTOR  — morning execution after pre-market scan
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class TradeRecord:
    ticker:   str
    strategy: str
    qty:      int
    price:    float
    stop:     float
    target:   float
    tt_score: int
    stage:    str
    rr:       float
    risk_amt: float   # dollar risk for this trade


class TradeExecutor:
    """
    After the morning scan produces top setups, execute qualifying orders.

    Rules:
      • Max 3 simultaneous open positions (Alpaca positions count)
      • Require TT ≥ 5 and Stage not 4-Bear
      • Position sizing: 1.5% portfolio risk per trade, capped at 15% portfolio value
        qty = floor(min(portfolio * 0.015 / risk_per_share,
                        portfolio * 0.15  / price))
      • Minimum qty = 1; skip if risk_per_share ≤ 0
    """

    MAX_POSITIONS = 3
    RISK_PCT      = 0.015    # 1.5% of portfolio per trade
    MAX_SIZE_PCT  = 0.15     # 15% of portfolio per position

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.broker: Optional[AlpacaBroker] = None
        if ALPACA_AVAILABLE and cfg.alpaca_key:
            try:
                self.broker = AlpacaBroker(cfg)
            except Exception as e:
                log.warning(f"TradeExecutor: broker unavailable — {e}")

    def _is_tradeable(self, symbol: str) -> bool:
        """Check Alpaca knows this asset and it's fractionable or tradeable."""
        if not self.broker:
            return True   # dry-run: assume tradeable
        try:
            asset = self.broker.api.get_asset(symbol)
            return asset.tradable
        except Exception:
            return False

    def _open_position_count(self) -> int:
        if not self.broker:
            return 0
        try:
            positions = self.broker.api.list_positions()
            return len(positions)
        except Exception:
            return 0

    def _already_in(self, symbol: str) -> bool:
        if not self.broker:
            return False
        return self.broker.current_position(symbol) > 0

    def _size_order(self, price: float, stop: float, portfolio: float) -> int:
        risk_per_share = price - stop
        if risk_per_share <= 0:
            return 0
        qty_risk = (portfolio * self.RISK_PCT) / risk_per_share
        qty_size = (portfolio * self.MAX_SIZE_PCT) / price
        return max(0, int(min(qty_risk, qty_size)))

    def execute(self, scan_results: list) -> list[TradeRecord]:
        """
        Execute orders for qualifying scan results.
        Returns a list of TradeRecord for each order placed (or attempted in dry-run).
        """
        executed: list[TradeRecord] = []

        if not self.broker:
            log.warning("TradeExecutor: no broker — dry-run only (logging signals).")

        open_count = self._open_position_count()
        log.info(f"TradeExecutor: {open_count} positions open, max={self.MAX_POSITIONS}")

        portfolio = 100_000.0   # fallback for dry-run
        if self.broker:
            try:
                portfolio = self.broker.portfolio_value()
            except Exception as e:
                log.warning(f"  Could not fetch portfolio value: {e}. Using ${portfolio:,.0f}.")

        for result in scan_results:
            if open_count >= self.MAX_POSITIONS:
                log.info(f"  Max positions reached ({self.MAX_POSITIONS}). Skipping remaining.")
                break

            ticker = result.ticker

            # Filter: Alpaca asset check (handles TSX and any unsupported symbol)
            if not self._is_tradeable(ticker):
                log.info(f"  {ticker}: not tradeable on Alpaca — skip.")
                continue

            # Filter: TT ≥ 5
            if result.tt_score < 5:
                log.info(f"  {ticker}: TT={result.tt_score}/7 < 5 — skip.")
                continue

            # Filter: not Stage 4
            if result.stage == "4-Bear":
                log.info(f"  {ticker}: Stage 4 — skip.")
                continue

            # Filter: already in position
            if self._already_in(ticker):
                log.info(f"  {ticker}: already have position — skip.")
                continue

            # Size order
            qty = self._size_order(result.price, result.stop, portfolio)
            if qty < 1:
                log.info(f"  {ticker}: qty=0 after sizing (price=${result.price:.2f} stop=${result.stop:.2f}) — skip.")
                continue

            risk_amt  = (result.price - result.stop) * qty
            sig_name  = result.signal_names()

            log.info(
                f"  → ORDER: BUY {qty} {ticker} @ ~${result.price:.2f}  "
                f"SL=${result.stop:.2f}  TP=${result.target:.2f}  "
                f"risk=${risk_amt:.2f}  [{sig_name}]"
            )

            if self.broker:
                try:
                    self.broker.api.submit_order(
                        symbol        = ticker,
                        qty           = qty,
                        side          = "buy",
                        type          = "market",
                        time_in_force = "day",
                    )
                    open_count += 1
                except Exception as e:
                    log.error(f"  {ticker}: order failed — {e}")
                    continue
            else:
                # Dry-run: count it anyway so we respect max-positions in log
                open_count += 1

            executed.append(TradeRecord(
                ticker   = ticker,
                strategy = sig_name,
                qty      = qty,
                price    = result.price,
                stop     = result.stop,
                target   = result.target,
                tt_score = result.tt_score,
                stage    = result.stage,
                rr       = result.rr(),
                risk_amt = risk_amt,
            ))

        log.info(f"TradeExecutor: {len(executed)} order(s) placed.")
        return executed

    # ── trade confirmation email ───────────────────────────────────────────────
    @staticmethod
    def _trades_html(trades: list[TradeRecord], date_str: str) -> tuple[str, str]:
        if not trades:
            rows  = '<tr><td colspan="7" class="neu" style="text-align:center">No qualifying trades today.</td></tr>'
        else:
            rows = ""
            for t in trades:
                rr_cls = "ok" if t.rr >= 2.5 else "warn" if t.rr >= 1.5 else "no"
                rows += f"""
                <tr>
                  <td><b>{t.ticker}</b></td>
                  <td>{t.qty}</td>
                  <td>${t.price:.2f}</td>
                  <td class="no">${t.stop:.2f}</td>
                  <td class="ok">${t.target:.2f}</td>
                  <td class="{rr_cls}">{t.rr}</td>
                  <td style="color:#9e9e9e;font-size:11px">{t.strategy}</td>
                </tr>
                <tr>
                  <td colspan="3" style="color:#9e9e9e;font-size:11px">
                    TT {t.tt_score}/7 · {t.stage}
                  </td>
                  <td colspan="4" style="color:#9e9e9e;font-size:11px">
                    Risk ${t.risk_amt:.2f}
                  </td>
                </tr>"""

        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
        <style>{_CSS}</style></head><body><div class="card">
        <h2>🚀 TRADE EXECUTION REPORT — {date_str}</h2>
        <p style="color:#9e9e9e;font-size:13px">
          Orders placed at open based on this morning's market scan.
          All market orders — actual fill prices may differ slightly.
        </p>
        <table>
          <tr>
            <td style="color:#9e9e9e">Ticker</td>
            <td style="color:#9e9e9e">Qty</td>
            <td style="color:#9e9e9e">Entry</td>
            <td style="color:#9e9e9e">Stop</td>
            <td style="color:#9e9e9e">Target</td>
            <td style="color:#9e9e9e">R:R</td>
            <td style="color:#9e9e9e">Setup</td>
          </tr>
          {rows}
        </table>
        <div class="rule" style="margin-top:12px">
          📋 Next steps:<br>
          □ Verify fills in Alpaca dashboard<br>
          □ Set price alerts at stop and target levels<br>
          □ Bot will monitor positions every 15 min and email if action needed
        </div>
        <p class="foot">QQQ Swing Suite · Trade Execution · {datetime.now().strftime('%Y-%m-%d %H:%M ET')}</p>
        </div></body></html>"""

        n     = len(trades)
        subj  = f"🚀 Trades Placed: {n} order{'s' if n != 1 else ''} — {date_str}"
        return subj, html


# ─────────────────────────────────────────────────────────────────────────────
# POSITION MONITOR  — 15-minute intraday scan of open positions
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class MonitorAction:
    ticker:  str
    action:  str   # "exit" | "trim" | "trail_stop" | "hold"
    reason:  str
    price:   float
    qty:     int


class PositionMonitor:
    """
    Runs every 15 minutes during market hours.

    For each open Alpaca position:
      1. Download recent daily data + today's 15-min intraday bar
      2. Recompute all indicators
      3. Check exit conditions:
         - Stop hit: current price ≤ stop (2×ATR)
         - Exit signal: EMA50 breakdown on volume OR Stage 4
      4. Check trim condition: current price ≥ target → trim 1/3
      5. Log and optionally execute + send alert email
    """

    def __init__(self, cfg: Config):
        self.cfg    = cfg
        self.broker: Optional[AlpacaBroker] = None
        if ALPACA_AVAILABLE and cfg.alpaca_key:
            try:
                self.broker = AlpacaBroker(cfg)
            except Exception as e:
                log.warning(f"PositionMonitor: broker unavailable — {e}")

    def _current_price(self, symbol: str) -> Optional[float]:
        """Fetch the most recent 15-min bar price."""
        try:
            bars = yf.download(
                symbol, period="1d", interval="15m",
                prepost=False, progress=False, auto_adjust=True
            )
            if isinstance(bars.columns, pd.MultiIndex):
                bars.columns = bars.columns.get_level_values(0)
            bars = bars.dropna(how="all")
            if bars.empty:
                return None
            return float(bars["Close"].iloc[-1])
        except Exception:
            return None

    def _trim(self, symbol: str, full_qty: int) -> int:
        """Sell 1/3 of position (floor). Returns shares sold."""
        trim_qty = max(1, full_qty // 3)
        if self.broker:
            try:
                self.broker.api.submit_order(
                    symbol=symbol, qty=trim_qty, side="sell",
                    type="market", time_in_force="day",
                )
                log.info(f"  TRIM: sold {trim_qty} of {full_qty} shares of {symbol}")
            except Exception as e:
                log.error(f"  TRIM failed for {symbol}: {e}")
        return trim_qty

    def _exit(self, symbol: str, qty: int) -> None:
        if self.broker:
            try:
                self.broker.api.submit_order(
                    symbol=symbol, qty=qty, side="sell",
                    type="market", time_in_force="day",
                )
                log.info(f"  EXIT: sold {qty} shares of {symbol}")
            except Exception as e:
                log.error(f"  EXIT failed for {symbol}: {e}")

    def scan(self) -> list[MonitorAction]:
        actions: list[MonitorAction] = []

        if not self.broker:
            log.warning("PositionMonitor: no broker — scan aborted.")
            return actions

        try:
            positions = self.broker.api.list_positions()
        except Exception as e:
            log.error(f"PositionMonitor: could not list positions — {e}")
            return actions

        if not positions:
            log.info("PositionMonitor: no open positions.")
            return actions

        log.info(f"PositionMonitor: checking {len(positions)} position(s)...")

        for pos in positions:
            symbol = pos.symbol
            qty    = int(float(pos.qty))

            try:
                # Daily data for indicators
                df = fetch_data(symbol, days=self.cfg.lookback_days)
                ind   = Indicators(df, self.cfg)
                strat = Strategies(ind)
                r     = ind.df.iloc[-1]

                # Current intraday price
                cur_price = self._current_price(symbol)
                if cur_price is None:
                    cur_price = float(pos.current_price or r["Close"])

                stop   = float(r["sl"])
                target = float(r["tp"])

                log.info(
                    f"  {symbol}: cur=${cur_price:.2f}  "
                    f"stop=${stop:.2f}  target=${target:.2f}  "
                    f"TT={int(r['tt_score'])}/7  stage2={bool(r['stage2'])}"
                )

                # Priority 1: Exit — stop hit
                if cur_price <= stop:
                    reason = f"Stop hit: ${cur_price:.2f} ≤ ${stop:.2f}"
                    log.info(f"  → EXIT ({reason})")
                    self._exit(symbol, qty)
                    actions.append(MonitorAction(symbol, "exit", reason, cur_price, qty))
                    continue

                # Priority 2: Exit — signal (EMA50 break or Stage 4)
                exit_s = strat.exit_signal()
                if exit_s:
                    reason = f"Exit signal: {exit_s.reason}"
                    log.info(f"  → EXIT ({reason})")
                    self._exit(symbol, qty)
                    actions.append(MonitorAction(symbol, "exit", reason, cur_price, qty))
                    continue

                # Priority 3: Trim at target
                if cur_price >= target and qty >= 2:
                    trimmed = self._trim(symbol, qty)
                    reason  = f"Target reached: ${cur_price:.2f} ≥ ${target:.2f} → sold {trimmed} shares"
                    log.info(f"  → TRIM ({reason})")
                    actions.append(MonitorAction(symbol, "trim", reason, cur_price, trimmed))
                    continue

                # Hold — check if any new entry signals worth noting
                new_sigs = strat.all_signals()
                if new_sigs:
                    sig_names = ", ".join(s.strategy for s in new_sigs)
                    reason = f"New signals: {sig_names}"
                    log.info(f"  → HOLD with new signal(s): {reason}")
                    actions.append(MonitorAction(symbol, "hold", reason, cur_price, qty))
                else:
                    log.info(f"  → HOLD — no action needed")

            except Exception as e:
                log.warning(f"  {symbol}: monitor error — {e}")
                continue

        log.info(f"PositionMonitor: {len(actions)} action(s) logged.")
        return actions

    # ── monitor alert email ────────────────────────────────────────────────────
    @staticmethod
    def _monitor_html(actions: list[MonitorAction], date_str: str) -> tuple[str, str]:
        if not actions:
            rows = '<tr><td colspan="4" class="neu" style="text-align:center">All positions holding — no action taken.</td></tr>'
        else:
            rows = ""
            for a in actions:
                if a.action == "exit":
                    cls, icon = "no",   "🚨 EXIT"
                elif a.action == "trim":
                    cls, icon = "warn", "✂️  TRIM"
                elif a.action == "hold":
                    cls, icon = "ok",   "⚡ SIGNAL"
                else:
                    cls, icon = "neu",  "HOLD"
                rows += f"""
                <tr>
                  <td><b>{a.ticker}</b></td>
                  <td class="{cls}">{icon}</td>
                  <td>${a.price:.2f}</td>
                  <td style="color:#b0bec5;font-size:12px">{a.reason}</td>
                </tr>"""

        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
        <style>{_CSS}</style></head><body><div class="card">
        <h2>📡 POSITION MONITOR — {date_str}</h2>
        <p style="color:#9e9e9e;font-size:13px">15-minute position check. Actions taken automatically.</p>
        <table>
          <tr>
            <td style="color:#9e9e9e">Ticker</td>
            <td style="color:#9e9e9e">Action</td>
            <td style="color:#9e9e9e">Price</td>
            <td style="color:#9e9e9e">Reason</td>
          </tr>
          {rows}
        </table>
        <p class="foot">QQQ Swing Suite · Monitor · {datetime.now().strftime('%Y-%m-%d %H:%M ET')}</p>
        </div></body></html>"""

        exits  = sum(1 for a in actions if a.action == "exit")
        trims  = sum(1 for a in actions if a.action == "trim")
        sigs   = sum(1 for a in actions if a.action == "hold")
        parts  = []
        if exits: parts.append(f"{exits} exit{'s' if exits > 1 else ''}")
        if trims: parts.append(f"{trims} trim{'s' if trims > 1 else ''}")
        if sigs:  parts.append(f"{sigs} new signal{'s' if sigs > 1 else ''}")
        summary = ", ".join(parts) if parts else "all holding"
        subj    = f"📡 Monitor Alert ({summary}) — {date_str}"
        return subj, html


class ReportBuilder:
    def __init__(self, cfg: Config):
        self.cfg     = cfg
        self.df      = fetch_data(cfg.symbol, cfg.lookback_days)
        self.vix_df  = fetch_data(cfg.vix_symbol, 10)
        self.ind     = Indicators(self.df, cfg)
        self.strat   = Strategies(self.ind)
        self.r       = self.ind.df.iloc[-1]
        self.prev    = self.ind.df.iloc[-2]
        self.vix     = float(self.vix_df["Close"].iloc[-1])
        self.date    = self.df.index[-1].strftime("%A, %B %d %Y")
        self.scanner = PreMarketScanner()
        self.mkt_scanner = MarketScanner(cfg)

    # ── top 5 market scan section ─────────────────────────────────────────────
    @staticmethod
    def _top5_html(results: list[ScanResult]) -> str:
        if not results:
            return '<div class="rule">No qualifying setups found in today\'s scan.</div>'
        rows = ""
        for r in results:
            stage_cls = "ok" if r.stage == "2-Bull" else "no" if r.stage == "4-Bear" else "warn"
            vol_cls   = "ok" if r.vol_surge else "neu"
            rows += f"""
            <tr>
              <td><b>{r.ticker}</b></td>
              <td>${r.price:.2f}</td>
              <td class="{stage_cls}">{r.stage}</td>
              <td class="{'ok' if r.tt_score >= 6 else 'warn'}">{r.tt_score}/7</td>
              <td class="{vol_cls}">{'✔' if r.vol_surge else '—'}</td>
              <td style="color:#b0bec5;font-size:11px">{r.signal_names()}</td>
            </tr>
            <tr>
              <td colspan="2" style="color:#9e9e9e;font-size:11px">
                Stop ${r.stop:.2f} · Target ${r.target:.2f} · R:R {r.rr()}
              </td>
              <td colspan="4" style="color:#9e9e9e;font-size:11px">RSI {r.rsi:.1f} · Score {r.score}</td>
            </tr>"""
        return f"""
        <table>
          <tr>
            <td style="color:#9e9e9e">Ticker</td>
            <td style="color:#9e9e9e">Price</td>
            <td style="color:#9e9e9e">Stage</td>
            <td style="color:#9e9e9e">TT</td>
            <td style="color:#9e9e9e">Vol</td>
            <td style="color:#9e9e9e">Signals</td>
          </tr>
          {rows}
        </table>"""

    # ── focus ticker section ──────────────────────────────────────────────────
    @staticmethod
    def _focus_section(prepost: bool = False) -> str:
        """Pull latest price, % change, RSI, and MACD for each focus ticker."""
        rows = ""
        for ticker in FOCUS_TICKERS:
            try:
                df = yf.download(ticker, period="60d", interval="1d",
                                 prepost=prepost, progress=False, auto_adjust=True)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.dropna()
                if len(df) < 30:
                    raise ValueError("not enough data")

                close     = df["Close"]
                price     = float(close.iloc[-1])
                prev      = float(close.iloc[-2])
                chg       = ((price - prev) / prev) * 100
                chg_cls   = "ok" if chg >= 0 else "no"
                arrow     = "▲" if chg >= 0 else "▼"

                # RSI
                delta = close.diff()
                gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
                loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
                rsi   = float(100 - (100 / (1 + gain / loss.replace(0, np.nan)))).iloc[-1] if hasattr((100 - (100 / (1 + gain / loss.replace(0, np.nan)))), 'iloc') else float((100 - (100 / (1 + gain / loss.replace(0, np.nan)))).iloc[-1])

                # Quick RSI calc
                rs_series = gain / loss.replace(0, np.nan)
                rsi_val   = float((100 - 100 / (1 + rs_series)).iloc[-1])
                rsi_cls   = "ok" if 50 < rsi_val < 70 else "warn" if 40 <= rsi_val <= 50 else "no"

                # MACD
                ema12  = close.ewm(span=12, adjust=False).mean()
                ema26  = close.ewm(span=26, adjust=False).mean()
                macd   = ema12 - ema26
                sig    = macd.ewm(span=9,  adjust=False).mean()
                m_bull = bool(macd.iloc[-1] > sig.iloc[-1] and macd.iloc[-1] > 0)
                m_cls  = "ok" if m_bull else "no"
                m_txt  = "Bull" if m_bull else "Bear"

                # Pre-market price if requested
                pm_txt = ""
                if prepost:
                    try:
                        pm = yf.download(ticker, period="1d", interval="1m",
                                         prepost=True, progress=False, auto_adjust=True)
                        if isinstance(pm.columns, pd.MultiIndex):
                            pm.columns = pm.columns.get_level_values(0)
                        pm_et  = pm.copy()
                        pm_et.index = pd.to_datetime(pm_et.index).tz_convert("America/New_York")
                        pm_bars = pm_et[
                            (pm_et.index.hour >= 4) &
                            ~((pm_et.index.hour == 9) & (pm_et.index.minute >= 30))
                        ]
                        if not pm_bars.empty:
                            pm_price = float(pm_bars["Close"].dropna().iloc[-1])
                            pm_chg   = ((pm_price - price) / price) * 100
                            pm_cls   = "ok" if pm_chg >= 0 else "no"
                            pm_txt   = f'<span class="{pm_cls}"> PM {pm_chg:+.1f}%</span>'
                    except Exception:
                        pass

                rows += f"""
                <tr>
                  <td><b>{ticker}</b></td>
                  <td>${price:.2f}{pm_txt}</td>
                  <td class="{chg_cls}">{arrow} {chg:+.2f}%</td>
                  <td class="{rsi_cls}">{rsi_val:.1f}</td>
                  <td class="{m_cls}">{m_txt}</td>
                </tr>"""
            except Exception as e:
                rows += f'<tr><td><b>{ticker}</b></td><td colspan="4" class="neu">unavailable</td></tr>'

        return f"""
        <table>
          <tr>
            <td style="color:#9e9e9e">Ticker</td>
            <td style="color:#9e9e9e">Price</td>
            <td style="color:#9e9e9e">Chg</td>
            <td style="color:#9e9e9e">RSI</td>
            <td style="color:#9e9e9e">MACD</td>
          </tr>
          {rows}
        </table>"""

    # ── shared movers table ───────────────────────────────────────────────────
    @staticmethod
    def _movers_table(movers: list[PreMarketMover], vol_label: str = "Volume") -> str:
        if not movers:
            return '<div class="rule">No bullish movers found — market may be closed or data unavailable.</div>'
        rows = ""
        for m in movers:
            rows += f"""
            <tr>
              <td><b>{m.ticker}</b></td>
              <td class="ok">▲ {m.pct_chg:+.2f}%</td>
              <td>${m.price:.2f}</td>
              <td style="color:#9e9e9e">{m.pm_volume:,}</td>
            </tr>"""
        return f"""
        <table>
          <tr>
            <td style="color:#9e9e9e">Ticker</td>
            <td style="color:#9e9e9e">Chg</td>
            <td style="color:#9e9e9e">Price</td>
            <td style="color:#9e9e9e">{vol_label}</td>
          </tr>{rows}
        </table>"""

    # ── shared snapshot rows ──────────────────────────────────────────────────
    def _snapshot_rows(self) -> str:
        r      = self.r
        score  = int(r["tt_score"])
        stage  = "2 — Bull" if r["stage2"] else "4 — Bear" if r["stage4"] else "1/3 — Neutral"
        s_cls  = "ok" if r["stage2"] else "no" if r["stage4"] else "warn"
        chg    = ((r["Close"] - self.prev["Close"]) / self.prev["Close"]) * 100
        chg_cls= "ok" if chg >= 0 else "no"

        return f"""
        <tr><td>Last Close</td>
            <td>${r['Close']:.2f} <span class="{chg_cls}">({chg:+.2f}%)</span></td></tr>
        <tr><td>Trend Template</td>
            <td class="{_score_class(score)}">{score}/7</td></tr>
        <tr><td>Stage (Weinstein)</td>
            <td class="{s_cls}">{stage}</td></tr>
        <tr><td>MACD</td>
            <td class="{'ok' if r['macd_bull'] else 'no'}">{'Bullish' if r['macd_bull'] else 'Bearish'}</td></tr>
        <tr><td>RSI ({self.cfg.rsi_len})</td>
            <td class="{'ok' if r['rsi_momentum'] else 'warn' if r['rsi_pullback'] else 'no'}">{r['rsi']:.1f}</td></tr>
        <tr><td>Volume Surge</td>
            <td class="{'ok' if r['vol_surge'] else 'neu'}">{'Yes' if r['vol_surge'] else 'No'}</td></tr>
        <tr><td>VCP Tight</td>
            <td class="{'ok' if r['vcp_ok'] else 'neu'}">{'Yes' if r['vcp_ok'] else 'No'}</td></tr>
        <tr><td>ATR Stop (2×)</td>
            <td>${r['sl']:.2f}</td></tr>
        <tr><td>ATR Target (3×)</td>
            <td>${r['tp']:.2f}</td></tr>
        """

    # ── pre-market report ─────────────────────────────────────────────────────
    def pre_market(self) -> tuple[str, str]:
        r       = self.r
        signals = self.strat.all_signals()
        exit_s  = self.strat.exit_signal()

        # Signals block
        sig_html = ""
        if exit_s:
            sig_html += f'<div class="exit">🚨 <b>EXIT SIGNAL</b> — {exit_s.reason}</div>'
        for s in signals:
            sig_html += f'<div class="sig">⚡ <b>{s.strategy}</b> — {s.reason}<br><small>Stop ${s.stop:.2f} · Target ${s.target:.2f} · R:R {s.rr()}</small></div>'
        if not sig_html:
            sig_html = '<div class="rule">No active entry signals. Stay patient.</div>'

        # Full market scan — top 5 setups
        top5        = self.mkt_scanner.scan(top_n=5)
        top5_html   = self._top5_html(top5)

        # Pre-market heat map — top 3 bullish movers
        movers      = self.scanner.scan()
        movers_html = self._movers_table(movers, vol_label="PM Vol")

        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
        <style>{_CSS}</style></head><body><div class="card">
        <h2>☀️ QQQ PRE-MARKET BRIEF — {self.date}</h2>

        <h3>VIX / Sizing Rule</h3>
        <table>
          <tr><td>VIX</td><td class="{_vix_class(self.vix)}">{self.vix:.2f}</td></tr>
        </table>
        <div class="rule">{_vix_rule(self.vix)}</div>

        <h3>🔍 Top 5 Market Setups Today</h3>
        {top5_html}

        <h3>🟢 Top 3 Bullish Pre-Market</h3>
        {movers_html}

        <h3>📌 Focus Tickers</h3>
        {self._focus_section(prepost=True)}

        <h3>QQQ Snapshot</h3>
        <table>{self._snapshot_rows()}</table>

        <h3>Signals</h3>
        {sig_html}

        <h3>Pre-Market Checklist</h3>
        <div class="rule">
          □ VIX sizing rule noted above<br>
          □ Major macro event today? (FOMC / CPI / Jobs / GDP) — if yes, no new entries until 30 min after<br>
          □ QQQ gap: {'⬆️ Gap UP' if r['Close'] > self.prev['Close'] else '⬇️ Gap DOWN'} from prior close ${self.prev['Close']:.2f}<br>
          □ Open positions above stop ({r['sl']:.2f})?<br>
          □ Any position up ≥10%? → Move stop to breakeven
        </div>

        <p class="foot">QQQ Swing Suite · Pre-Market · {datetime.now().strftime('%Y-%m-%d %H:%M ET')}</p>
        </div></body></html>"""

        subject = f"☀️ QQQ Pre-Market  {self.date}  |  TT {int(r['tt_score'])}/7  |  VIX {self.vix:.1f}"
        return subject, html

    # ── mid-day report ────────────────────────────────────────────────────────
    def mid_day(self) -> tuple[str, str]:
        r     = self.r
        score = int(r["tt_score"])

        # Intraday price (best effort via yfinance 1m)
        try:
            intra  = yf.download(self.cfg.symbol, period="1d", interval="5m",
                                 progress=False, auto_adjust=True)
            if isinstance(intra.columns, pd.MultiIndex):
                intra.columns = intra.columns.get_level_values(0)
            cur_price = float(intra["Close"].dropna().iloc[-1])
            day_open  = float(intra["Open"].dropna().iloc[0])
            intra_chg = ((cur_price - day_open) / day_open) * 100
            intra_vol = int(intra["Volume"].sum())
            vol_pct   = (intra_vol / float(r["vol_ma"])) * 100 if r["vol_ma"] > 0 else 0
        except Exception:
            cur_price = r["Close"]
            intra_chg = 0.0
            intra_vol = 0
            vol_pct   = 0

        chg_cls   = "ok" if intra_chg >= 0 else "no"
        vol_cls   = "ok" if vol_pct >= 150 else "warn" if vol_pct >= 80 else "no"
        dist_day  = intra_chg < 0 and vol_pct >= 150
        dist_warn = '<div class="exit">⚠️ <b>Potential Distribution Day</b> — QQQ down on heavy volume. Add to monthly tally.</div>' if dist_day else ""

        # Intraday bullish movers
        intra_movers      = self.scanner.intraday_scan()
        intra_movers_html = self._movers_table(intra_movers, vol_label="Intraday Vol")

        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
        <style>{_CSS}</style></head><body><div class="card">
        <h2>📊 QQQ MID-DAY CHECK — {self.date}</h2>

        <h3>Intraday Snapshot</h3>
        <table>
          <tr><td>Current Price</td>
              <td>${cur_price:.2f} <span class="{chg_cls}">({intra_chg:+.2f}% vs open)</span></td></tr>
          <tr><td>Intraday Volume</td>
              <td class="{vol_cls}">{intra_vol:,} ({vol_pct:.0f}% of daily avg)</td></tr>
          <tr><td>VIX</td>
              <td class="{_vix_class(self.vix)}">{self.vix:.2f}</td></tr>
        </table>
        {dist_warn}

        <h3>🟢 Top 3 Bullish Intraday</h3>
        {intra_movers_html}

        <h3>📌 Focus Tickers</h3>
        {self._focus_section(prepost=False)}

        <h3>Daily Indicators</h3>
        <table>{self._snapshot_rows()}</table>

        <h3>Mid-Day Checklist</h3>
        <div class="rule">
          □ QQQ holding above EMA21 (${r['ema21']:.2f})?  Current: ${cur_price:.2f}<br>
          □ EMA50 (${r['ema50']:.2f}) intact?<br>
          □ Any open position down > 5% intraday? → review stop<br>
          □ Distribution day? {'⚠️ YES — tally it' if dist_day else '✅ Not yet'}<br>
          □ Stage still {'2 — Bull ✅' if r['stage2'] else '4 — Bear ⛔' if r['stage4'] else '1/3 Neutral ⚠️'}
        </div>

        <p class="foot">QQQ Swing Suite · Mid-Day · {datetime.now().strftime('%Y-%m-%d %H:%M ET')}</p>
        </div></body></html>"""

        subject = f"📊 QQQ Mid-Day  {self.date}  |  ${cur_price:.2f} ({intra_chg:+.1f}%)  |  VIX {self.vix:.1f}"
        return subject, html


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    cfg = Config(
        symbol           = "QQQ",
        paper_trading    = True,
        en_momentum      = True,
        en_darvas        = True,
        en_vcp           = True,
        en_pullback      = True,
        en_break_bounce  = True,
        en_touch_turn    = True,
        atr_sl_mult      = 2.0,
        atr_tp_mult      = 3.0,
        max_position_pct = 0.15,
    )

    mode   = sys.argv[1] if len(sys.argv) > 1 else "--run"
    mailer = Emailer(cfg)

    if mode == "--premarket":
        log.info("Building pre-market report...")
        rpt            = ReportBuilder(cfg)
        subject, html  = rpt.pre_market()
        log.info(f"Subject: {subject}")
        mailer.send(subject, html)

    elif mode == "--midday":
        log.info("Building mid-day report...")
        rpt            = ReportBuilder(cfg)
        subject, html  = rpt.mid_day()
        log.info(f"Subject: {subject}")
        mailer.send(subject, html)

    elif mode == "--run":
        bot     = TradingBot(cfg)
        signals = bot.run()

    elif mode == "--trade":
        # ── Morning trade execution ─────────────────────────────────────────
        # 1. Run full market scan (same as pre-market email)
        # 2. Execute qualifying orders via Alpaca
        # 3. Send trade confirmation email
        log.info("=== TRADE EXECUTION MODE ===")
        date_str = datetime.now(pytz.timezone("America/New_York")).strftime("%A, %B %d %Y")

        scanner  = MarketScanner(cfg)
        top5     = scanner.scan(top_n=5)

        executor = TradeExecutor(cfg)
        trades   = executor.execute(top5)

        subject, html = TradeExecutor._trades_html(trades, date_str)
        log.info(f"Subject: {subject}")
        mailer.send(subject, html)

    elif mode == "--monitor":
        # ── 15-minute scan + position monitor ──────────────────────────────
        # Every 15 min this does three things in order:
        #   1. Load pre-market scan cache → execute new trades if under limit
        #   2. Check all open positions for exits / trims / new signals
        #   3. Email if any action was taken
        log.info("=== MONITOR MODE (15-min) ===")
        et       = pytz.timezone("America/New_York")
        now_et   = datetime.now(et)
        date_str = now_et.strftime("%A, %B %d %Y")
        time_str = now_et.strftime("%H:%M ET")

        # ── Step 1: new entries from cached pre-market scan ─────────────────
        new_trades: list[TradeRecord] = []
        cached = _load_scan_cache()
        if cached:
            executor   = TradeExecutor(cfg)
            new_trades = executor.execute(cached)
        else:
            log.info("  No cached scan — skipping new-entry check.")

        # ── Step 2: existing position checks ───────────────────────────────
        monitor = PositionMonitor(cfg)
        actions = monitor.scan()

        # ── Step 3: email when anything notable happened ────────────────────
        notable_actions = [a for a in actions if a.action in ("exit", "trim", "hold")]
        if new_trades or notable_actions:
            # Build combined email
            trade_rows = ""
            if new_trades:
                for t in new_trades:
                    rr_cls = "ok" if t.rr >= 2.5 else "warn" if t.rr >= 1.5 else "no"
                    trade_rows += f"""
                    <tr>
                      <td><b>{t.ticker}</b></td>
                      <td>{t.qty}</td>
                      <td>${t.price:.2f}</td>
                      <td class="no">${t.stop:.2f}</td>
                      <td class="ok">${t.target:.2f}</td>
                      <td class="{rr_cls}">{t.rr}</td>
                      <td style="color:#9e9e9e;font-size:11px">{t.strategy}</td>
                    </tr>"""
                trade_section = f"""
                <h3>🚀 New Trades Executed</h3>
                <table>
                  <tr>
                    <td style="color:#9e9e9e">Ticker</td>
                    <td style="color:#9e9e9e">Qty</td>
                    <td style="color:#9e9e9e">Entry</td>
                    <td style="color:#9e9e9e">Stop</td>
                    <td style="color:#9e9e9e">Target</td>
                    <td style="color:#9e9e9e">R:R</td>
                    <td style="color:#9e9e9e">Setup</td>
                  </tr>{trade_rows}
                </table>"""
            else:
                trade_section = ""

            _, monitor_html_body = PositionMonitor._monitor_html(actions, date_str)
            # Extract just the table from monitor html (reuse helper)
            mon_rows = ""
            for a in actions:
                if a.action == "exit":
                    cls, icon = "no",   "🚨 EXIT"
                elif a.action == "trim":
                    cls, icon = "warn", "✂️  TRIM"
                elif a.action == "hold":
                    cls, icon = "ok",   "⚡ SIGNAL"
                else:
                    continue
                mon_rows += f"""
                <tr>
                  <td><b>{a.ticker}</b></td>
                  <td class="{cls}">{icon}</td>
                  <td>${a.price:.2f}</td>
                  <td style="color:#b0bec5;font-size:12px">{a.reason}</td>
                </tr>"""

            monitor_section = ""
            if mon_rows:
                monitor_section = f"""
                <h3>📡 Position Updates</h3>
                <table>
                  <tr>
                    <td style="color:#9e9e9e">Ticker</td>
                    <td style="color:#9e9e9e">Action</td>
                    <td style="color:#9e9e9e">Price</td>
                    <td style="color:#9e9e9e">Reason</td>
                  </tr>{mon_rows}
                </table>"""

            html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
            <style>{_CSS}</style></head><body><div class="card">
            <h2>⏱ 15-MIN UPDATE — {date_str} {time_str}</h2>
            {trade_section}
            {monitor_section}
            <p class="foot">QQQ Swing Suite · Monitor · {now_et.strftime('%Y-%m-%d %H:%M ET')}</p>
            </div></body></html>"""

            n_t = len(new_trades)
            n_a = len(notable_actions)
            parts = []
            if n_t: parts.append(f"{n_t} trade{'s' if n_t > 1 else ''}")
            if n_a: parts.append(f"{n_a} position update{'s' if n_a > 1 else ''}")
            subject = f"⏱ {' · '.join(parts)} — {time_str}"
            log.info(f"Subject: {subject}")
            mailer.send(subject, html)
        else:
            log.info("Monitor: all clear — no action taken, no email sent.")

    else:
        print("Usage: python3 trading_bot.py [--premarket | --midday | --run | --trade | --monitor]")

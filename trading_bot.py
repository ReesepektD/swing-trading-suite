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

        sig_html = ""
        if exit_s:
            sig_html += f'<div class="exit">🚨 <b>EXIT SIGNAL</b> — {exit_s.reason}</div>'
        for s in signals:
            sig_html += f'<div class="sig">⚡ <b>{s.strategy}</b> — {s.reason}<br><small>Stop ${s.stop:.2f} · Target ${s.target:.2f} · R:R {s.rr()}</small></div>'
        if not sig_html:
            sig_html = '<div class="rule">No active entry signals. Stay patient.</div>'

        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
        <style>{_CSS}</style></head><body><div class="card">
        <h2>☀️ QQQ PRE-MARKET BRIEF — {self.date}</h2>

        <h3>VIX / Sizing Rule</h3>
        <table>
          <tr><td>VIX</td><td class="{_vix_class(self.vix)}">{self.vix:.2f}</td></tr>
        </table>
        <div class="rule">{_vix_rule(self.vix)}</div>

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

    mode = sys.argv[1] if len(sys.argv) > 1 else "--run"
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

    else:
        print("Usage: python3 trading_bot.py [--premarket | --midday | --run]")

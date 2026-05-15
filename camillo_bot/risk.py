"""
Risk management for the Camillo bot.

Handles:
  - Position sizing (Camillo's tiered conviction model)
  - Exit signal generation (stop loss, take profit, time, trend peak)
  - Portfolio-level kill switch (max drawdown halt)
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional

from .config import Config
from .database import DBPosition

log = logging.getLogger(__name__)


class ExitReason(Enum):
    STOP_LOSS        = "stop_loss"        # price fell 15%+ from entry
    TAKE_PROFIT_HALF = "take_profit_half" # price doubled → sell 50%
    TAKE_PROFIT_FULL = "take_profit_full" # sell remaining after half-out
    TREND_PEAK       = "trend_peak"       # Google Trends slope went negative
    LOW_SCORE        = "low_score"        # re-scan composite score fell below floor
    MAX_HOLD         = "max_hold"         # 26-week time limit reached
    MANUAL           = "manual"


@dataclass
class ExitSignal:
    ticker:   str
    reason:   ExitReason
    partial:  bool    # True = sell half, False = sell full position
    note:     str


@dataclass
class SizeResult:
    ticker:   str
    notional: float   # dollar amount to buy
    pct:      float   # fraction of portfolio


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------

def size_order(
    ticker:        str,
    signal:        str,     # "BUY" | "WATCH"
    score:         float,
    account_equity: float,
    open_positions: int,
    config:        Config,
) -> Optional[SizeResult]:
    """
    Camillo's conviction-based sizing:
      BUY   → up to buy_position_pct of equity
      WATCH → up to watch_position_pct (only entered manually / high-conviction override)
    Scales down slightly with score so a 70 gets less than a 90.
    """
    if open_positions >= config.max_positions:
        log.warning("Max positions (%d) reached — skipping %s", config.max_positions, ticker)
        return None

    if signal == "BUY":
        base_pct = config.buy_position_pct
    elif signal == "WATCH":
        base_pct = config.watch_position_pct
    else:
        return None

    # Scale by score: score 70 → 70%, score 100 → 100% of base_pct
    scale   = min(score / 100.0, 1.0)
    pct     = base_pct * scale
    notional = account_equity * pct

    # Floor: don't place tiny orders
    if notional < 50:
        log.info("Order too small ($%.2f) — skipping %s", notional, ticker)
        return None

    return SizeResult(ticker=ticker, notional=round(notional, 2), pct=round(pct, 4))


# ---------------------------------------------------------------------------
# Exit logic
# ---------------------------------------------------------------------------

def check_exits(
    db_pos:        DBPosition,
    current_price: float,
    trend_score:   Optional[float],  # None if trend not re-checked today
    rescan_score:  Optional[float],  # None if full rescan not run
    config:        Config,
) -> Optional[ExitSignal]:
    """
    Evaluate all exit conditions in priority order.
    Returns the first matching ExitSignal, or None if position should be held.

    Priority:
      1. Stop loss          (hardest rule — protect capital first)
      2. Max hold time      (discipline — trends don't last forever)
      3. Take profit (half) (lock in gains at 100%)
      4. Trend peak         (Camillo's primary signal)
      5. Low composite score (full re-scan fell below floor)
    """
    ret = (current_price / db_pos.entry_price) - 1.0

    # 1. Hard stop loss
    if ret <= -config.stop_loss_pct:
        return ExitSignal(
            ticker  = db_pos.ticker,
            reason  = ExitReason.STOP_LOSS,
            partial = False,
            note    = f"Stop loss triggered: {ret:+.1%} from entry",
        )

    # 2. Max hold (26 weeks)
    try:
        entry = date.fromisoformat(db_pos.entry_date)
        weeks_held = (date.today() - entry).days / 7
        if weeks_held >= config.max_hold_weeks:
            return ExitSignal(
                ticker  = db_pos.ticker,
                reason  = ExitReason.MAX_HOLD,
                partial = False,
                note    = f"Max hold reached: {weeks_held:.1f} weeks",
            )
    except ValueError:
        pass

    # 3. Take profit — sell half when position doubles
    if ret >= config.take_profit_half_pct and not db_pos.half_taken:
        return ExitSignal(
            ticker  = db_pos.ticker,
            reason  = ExitReason.TAKE_PROFIT_HALF,
            partial = True,
            note    = f"Take profit: position up {ret:+.1%} — selling 50%",
        )

    # 4. Trend peak (Google Trends slope negative)
    if trend_score is not None and trend_score < 5.0:
        return ExitSignal(
            ticker  = db_pos.ticker,
            reason  = ExitReason.TREND_PEAK,
            partial = False,
            note    = f"Trend peaked: score {trend_score:.1f} (was {db_pos.last_trend_score:.1f})",
        )

    # 5. Re-scan composite score fell below floor
    if rescan_score is not None and rescan_score < config.exit_score_floor:
        return ExitSignal(
            ticker  = db_pos.ticker,
            reason  = ExitReason.LOW_SCORE,
            partial = False,
            note    = f"Score fell to {rescan_score:.1f} (floor {config.exit_score_floor})",
        )

    return None


# ---------------------------------------------------------------------------
# Portfolio-level kill switch
# ---------------------------------------------------------------------------

def should_halt_entries(
    current_equity:   float,
    peak_equity:      float,
    config:           Config,
) -> bool:
    """
    Pause all new entries if portfolio is down max_drawdown_halt from its peak.
    Does NOT close existing positions — only blocks new buys.
    """
    if peak_equity <= 0:
        return False
    drawdown = (current_equity - peak_equity) / peak_equity
    if drawdown <= -config.max_drawdown_halt:
        log.warning(
            "DRAWDOWN HALT: portfolio down %.1f%% from peak — pausing new entries",
            drawdown * 100,
        )
        return True
    return False


# ---------------------------------------------------------------------------
# Trend re-check throttle
# ---------------------------------------------------------------------------

def needs_trend_recheck(db_pos: DBPosition, config: Config) -> bool:
    """Return True if it's been more than trend_recheck_days since last check."""
    if not db_pos.last_trend_check:
        return True
    try:
        last = date.fromisoformat(db_pos.last_trend_check)
        return (date.today() - last).days >= config.trend_recheck_days
    except ValueError:
        return True

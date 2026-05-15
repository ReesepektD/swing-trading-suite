"""
Executor: translates signals into broker orders and manages position lifecycle.

Entry flow:  ArbitrageSignal → size_order → place_buy → save_position → log_trade
Exit flow:   DBPosition → check_exits → place_sell → close_position → log_trade
"""

import logging
import sys
import os
from datetime import date, datetime
from typing import Optional

from .broker import BaseBroker
from .config import Config
from .database import Database, DBPosition, TradeLog
from .risk import (
    ExitReason, ExitSignal,
    check_exits, needs_trend_recheck, should_halt_entries, size_order,
)

# Import scanner from parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from camillo_social_arbitrage import SocialArbitrageScanner, ArbitrageSignal

log = logging.getLogger(__name__)


class Executor:
    def __init__(self, broker: BaseBroker, db: Database, config: Config):
        self.broker  = broker
        self.db      = db
        self.config  = config
        self._scanner = SocialArbitrageScanner(
            reddit_creds={
                "client_id":     config.reddit_client_id,
                "client_secret": config.reddit_client_secret,
            } if config.reddit_client_id else None
        )
        self._peak_equity: float = 0.0

    # ------------------------------------------------------------------
    # Entry pipeline
    # ------------------------------------------------------------------

    def run_entries(self, signals: list) -> list:
        """
        For each BUY signal, check risk limits and place an order.
        Returns list of tickers where orders were submitted.
        """
        account = self.broker.get_account()
        self._peak_equity = max(self._peak_equity, account.equity)

        if should_halt_entries(account.equity, self._peak_equity, self.config):
            log.warning("Entry halt active — skipping all buys")
            return []

        open_positions   = self.db.get_all_positions()
        open_tickers     = {p.ticker for p in open_positions}
        executed         = []

        for sig in signals:
            if sig.signal not in ("BUY", "WATCH"):
                continue
            if sig.ticker in open_tickers:
                log.info("Already holding %s — skipping entry", sig.ticker)
                continue

            size = size_order(
                ticker         = sig.ticker,
                signal         = sig.signal,
                score          = sig.composite_score,
                account_equity = account.equity,
                open_positions = len(open_positions) + len(executed),
                config         = self.config,
            )
            if not size:
                continue

            try:
                price = self.broker.get_price(sig.ticker)
                order = self.broker.place_buy(sig.ticker, size.notional)

                db_pos = DBPosition(
                    ticker           = sig.ticker,
                    entry_date       = date.today().isoformat(),
                    entry_price      = price,
                    notional         = size.notional,
                    keywords         = sig.keywords,
                    entry_score      = sig.composite_score,
                    signal           = sig.signal,
                    half_taken       = False,
                    last_trend_check = date.today().isoformat(),
                    last_trend_score = sig.trend_velocity_score,
                )
                self.db.save_position(db_pos)
                self.db.log_trade(TradeLog(
                    ticker    = sig.ticker,
                    action    = "ENTRY",
                    reason    = "signal",
                    price     = price,
                    notional  = size.notional,
                    score     = sig.composite_score,
                    timestamp = datetime.now().isoformat(),
                ))
                executed.append(sig.ticker)
                log.info(
                    "ENTRY %s | score %.1f | $%.2f (%.1f%% of portfolio)",
                    sig.ticker, sig.composite_score, size.notional, size.pct * 100,
                )

            except Exception as e:
                log.error("Entry failed for %s: %s", sig.ticker, e)

        return executed

    # ------------------------------------------------------------------
    # Exit pipeline
    # ------------------------------------------------------------------

    def run_exits(self) -> list:
        """
        Check all open positions against exit conditions.
        Executes sells where triggered.
        Returns list of tickers acted on.
        """
        positions = self.db.get_all_positions()
        acted     = []

        for db_pos in positions:
            try:
                exit_signal = self._evaluate_exit(db_pos)
                if not exit_signal:
                    continue

                broker_pos = self.broker.get_position(db_pos.ticker)
                if not broker_pos:
                    log.warning("%s not found in broker — removing from DB", db_pos.ticker)
                    self.db.close_position(db_pos.ticker)
                    continue

                current_price = self.broker.get_price(db_pos.ticker)

                if exit_signal.partial:
                    # Sell half by notional
                    half_value = broker_pos.market_value / 2
                    order = self.broker.place_sell_notional(db_pos.ticker, half_value)
                    self.db.mark_half_taken(db_pos.ticker)
                    action = "PARTIAL_EXIT"
                    notional = half_value
                else:
                    order = self.broker.place_sell(db_pos.ticker, broker_pos.qty)
                    self.db.close_position(db_pos.ticker)
                    action = "EXIT"
                    notional = broker_pos.market_value

                self.db.log_trade(TradeLog(
                    ticker    = db_pos.ticker,
                    action    = action,
                    reason    = exit_signal.reason.value,
                    price     = current_price,
                    notional  = notional,
                    score     = db_pos.last_trend_score,
                    timestamp = datetime.now().isoformat(),
                ))
                acted.append(db_pos.ticker)
                log.info(
                    "%s %s | %s | $%.2f",
                    action, db_pos.ticker, exit_signal.note, notional,
                )

            except Exception as e:
                log.error("Exit check failed for %s: %s", db_pos.ticker, e)

        return acted

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _evaluate_exit(self, db_pos: DBPosition) -> Optional[ExitSignal]:
        """
        Run all exit checks for one position.
        Re-fetches trend data on a throttled schedule.
        """
        current_price = self.broker.get_price(db_pos.ticker)

        # Throttled trend re-check (weekly by default)
        trend_score  = None
        rescan_score = None

        if needs_trend_recheck(db_pos, self.config):
            log.info("Re-checking trend for %s...", db_pos.ticker)
            try:
                fresh_sig = self._scanner.score_ticker(db_pos.ticker, db_pos.keywords)
                trend_score  = fresh_sig.trend_velocity_score
                rescan_score = fresh_sig.composite_score
                self.db.update_trend_check(db_pos.ticker, trend_score)
                log.info(
                    "%s re-scan: composite %.1f | trend %.1f",
                    db_pos.ticker, rescan_score, trend_score,
                )
            except Exception as e:
                log.warning("Trend re-check failed for %s: %s", db_pos.ticker, e)

        return check_exits(
            db_pos        = db_pos,
            current_price = current_price,
            trend_score   = trend_score,
            rescan_score  = rescan_score,
            config        = self.config,
        )

    def print_portfolio_summary(self):
        """Log current portfolio state to stdout."""
        positions = self.db.get_all_positions()
        account   = self.broker.get_account()

        print("\n" + "═" * 68)
        print(f"  CAMILLO BOT — PORTFOLIO SUMMARY  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"  Equity: ${account.equity:>12,.2f}   Cash: ${account.cash:>12,.2f}")
        print("═" * 68)

        if not positions:
            print("  No open positions.")
        else:
            for p in positions:
                try:
                    bp  = self.broker.get_position(p.ticker)
                    ret = ((bp.avg_cost and (self.broker.get_price(p.ticker) / bp.avg_cost - 1)) or 0)
                    half_flag = " [½ taken]" if p.half_taken else ""
                    print(f"  {p.ticker:<6}  entry ${p.entry_price:.2f}  "
                          f"score {p.entry_score:.0f}  ret {ret:+.1%}{half_flag}")
                except Exception:
                    print(f"  {p.ticker:<6}  (price unavailable)")

        summary = self.db.get_summary()
        print("─" * 68)
        print(f"  Total trades: {summary.get('exits', 0)} closed  |  "
              f"Deployed: ${(summary.get('total_deployed') or 0):,.0f}  |  "
              f"Returned: ${(summary.get('total_returned') or 0):,.0f}")
        print("═" * 68 + "\n")

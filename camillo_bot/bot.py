"""
CamilloBot — main orchestrator.

Schedule:
  09:45 ET  run_scan()       → score watchlist, execute entries
  12:00 ET  run_exit_check() → check stop loss, take profit, trend
  15:30 ET  run_exit_check() → final check before close + daily summary
  (weekdays only, market must be open)

Run modes (set via run_bot.py):
  dry     → DryRunBroker, no real orders
  paper   → AlpacaBroker(paper=True)
  live    → AlpacaBroker(paper=False) — requires CAMILLO_LIVE_CONFIRM env var
"""

import logging
import sys
import os
import time
from datetime import datetime, date

import zoneinfo

from .broker import AlpacaBroker, DryRunBroker, BaseBroker
from .config import Config
from .database import Database
from .executor import Executor
from .notifier import EmailNotifier

# Import scanners from parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from camillo_social_arbitrage import SocialArbitrageScanner
from markov_regime import MarkovHedgeScanner

log = logging.getLogger(__name__)
ET  = zoneinfo.ZoneInfo("America/New_York")


class CamilloBot:
    def __init__(self, broker: BaseBroker, config: Config, db: Database,
                 notifier: EmailNotifier = None):
        self.broker   = broker
        self.config   = config
        self.db       = db
        self.executor = Executor(broker, db, config)
        self.notifier = notifier or EmailNotifier.from_env()
        self._scanner = SocialArbitrageScanner(
            reddit_creds={
                "client_id":     config.reddit_client_id,
                "client_secret": config.reddit_client_secret,
            } if config.reddit_client_id else None
        )
        self._markov = MarkovHedgeScanner()

    # ------------------------------------------------------------------
    # Core jobs
    # ------------------------------------------------------------------

    def run_scan(self, force: bool = False):
        """
        Morning job: score the watchlist and enter qualifying positions.
        Skips if market is closed (pass force=True to override for testing).
        """
        if not force and not self.broker.is_market_open():
            log.info("Market closed — skipping scan (use --force to override)")
            return

        log.info("━━━ MORNING SCAN ━━━  %s", datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"))
        print(f"\n[{datetime.now(ET).strftime('%H:%M')}] Running morning scan on {len(self.config.watchlist)} tickers...")

        results = self._scanner.scan_watchlist(self.config.watchlist)
        if results.empty:
            log.warning("Scan returned no results")
            return

        # Markov regime filter: build a set of tickers currently in Bull regime
        try:
            markov_results = self._markov.scan_watchlist(self.config.watchlist)
            bull_tickers = set(
                markov_results.loc[markov_results["Regime"] == "Bull", "Ticker"].tolist()
            )
            log.info("Markov Bull regime: %s", bull_tickers or "none")

            # Print regime table inline
            icons = {"BUY": "●", "WATCH": "◐", "PASS": "○"}
            print(f"\n{'─'*60}")
            print("  REGIME (Markov)")
            print(f"{'─'*60}")
            for _, r in markov_results.iterrows():
                icon  = icons.get(r["Signal"], "?")
                badge = "✓ Bull" if r["Regime"] == "Bull" else ("✗ Bear" if r["Regime"] == "Bear" else "~ Sideways")
                print(f"  {icon} {r['Ticker']:<6}  {badge:<12}  "
                      f"Score {r['Score']:.1f}  →Bull {r['→Bull']}  →Bear {r['→Bear']}")
            print(f"{'─'*60}\n")
        except Exception as exc:
            log.warning("Markov scan failed — proceeding without regime filter: %s", exc)
            bull_tickers = None  # None = filter disabled

        # Filter to actionable signals; skip if regime is Bear (not Bull or Sideways)
        buy_signals = []
        for _, row in results.iterrows():
            if row["Signal"] in ("BUY", "WATCH") and row["Composite"] >= self.config.min_buy_score:
                ticker = row["Ticker"]
                if bull_tickers is not None and ticker not in bull_tickers:
                    log.info("Skipping %s — Markov regime is not Bull", ticker)
                    continue
                item = next(
                    (w for w in self.config.watchlist if w["ticker"] == ticker), None
                )
                if item:
                    sig = self._scanner.score_ticker(ticker, item["keywords"])
                    buy_signals.append(sig)

        if buy_signals:
            log.info("%d buy signal(s) found: %s", len(buy_signals), [s.ticker for s in buy_signals])
            entered = self.executor.run_entries(buy_signals)
            log.info("Entries executed: %s", entered or "none")
        else:
            log.info("No qualifying signals today (min score %.0f)", self.config.min_buy_score)

        self.executor.print_portfolio_summary()

        if self.notifier:
            try:
                account   = self.broker.get_account()
                positions = self.db.get_all_positions()
                self.notifier.send_scan_report(results, positions, account.equity)
            except Exception as exc:
                log.warning("Scan email failed: %s", exc)

    def run_exit_check(self, force: bool = False):
        """
        Intraday/close job: evaluate exit conditions on all open positions.
        Skips if market is closed (pass force=True to override for testing).
        """
        if not force and not self.broker.is_market_open():
            log.info("Market closed — skipping exit check (use --force to override)")
            return

        positions = self.db.get_all_positions()
        if not positions:
            log.info("No open positions to check")
            return

        log.info("━━━ EXIT CHECK ━━━  %s  (%d positions)",
                 datetime.now(ET).strftime("%H:%M ET"), len(positions))

        acted = self.executor.run_exits()
        if acted:
            log.info("Exit actions taken: %s", acted)
        else:
            log.info("All positions held — no exit conditions triggered")

    def run_daily_summary(self):
        """End-of-day portfolio snapshot."""
        log.info("━━━ DAILY SUMMARY ━━━  %s", date.today().isoformat())
        self.executor.print_portfolio_summary()

        if self.notifier:
            try:
                account   = self.broker.get_account()
                positions = self.db.get_all_positions()
                self.notifier.send_daily_summary(positions, account.equity)
            except Exception as exc:
                log.warning("Daily summary email failed: %s", exc)

    def run_regime_scan(self):
        """Print a Markov regime table for the full watchlist and exit."""
        log.info("━━━ REGIME SCAN ━━━  %s", date.today().isoformat())
        results = self._markov.scan_watchlist(self.config.watchlist)
        if results.empty:
            print("No regime results returned.")
            return
        print("\n" + "═" * 68)
        print("  MARKOV REGIME SCAN")
        print("═" * 68)
        for _, row in results.iterrows():
            icon = {"BUY": "●", "WATCH": "◐", "PASS": "○"}.get(row["Signal"], "?")
            print(f"\n  {icon} {row['Ticker']:<6}  [{row['Signal']}]  "
                  f"Score {row['Score']:.1f}  Regime: {row['Regime']}")
            print(f"     {row['→Bull']} Bull  {row['→Bear']} Bear")
        print("\n" + "═" * 68 + "\n")

    # ------------------------------------------------------------------
    # Scheduler loop
    # ------------------------------------------------------------------

    def start(self):
        """
        Blocking event loop. Checks the clock every minute and fires jobs
        when the scheduled time is reached (Eastern Time, weekdays only).
        """
        log.info("CamilloBot started — watching for scheduled jobs")
        log.info("Scan time: %s ET  |  Exit checks: %s ET",
                 self.config.scan_time, ", ".join(self.config.exit_check_times))

        fired_today: set = set()

        while True:
            now_et     = datetime.now(ET)
            now_hhmm   = now_et.strftime("%H:%M")
            today_str  = now_et.strftime("%Y-%m-%d")
            is_weekday = now_et.weekday() < 5

            # Reset fired set at midnight
            if now_hhmm == "00:00":
                fired_today.clear()

            if is_weekday:
                job_key = f"{today_str}:{now_hhmm}"

                if now_hhmm == self.config.scan_time and job_key not in fired_today:
                    fired_today.add(job_key)
                    self._safe_run(self.run_scan, "run_scan")

                for check_time in self.config.exit_check_times:
                    job_key = f"{today_str}:{now_hhmm}:{check_time}"
                    if now_hhmm == check_time and job_key not in fired_today:
                        fired_today.add(job_key)
                        self._safe_run(self.run_exit_check, "run_exit_check")

                # End of day summary
                eod_key = f"{today_str}:eod"
                if now_hhmm == "16:05" and eod_key not in fired_today:
                    fired_today.add(eod_key)
                    self._safe_run(self.run_daily_summary, "run_daily_summary")

            time.sleep(60)

    def _safe_run(self, fn, name: str):
        try:
            fn()
        except Exception as e:
            log.error("Job %s failed: %s", name, e, exc_info=True)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_bot(mode: str = "dry", config: Config = None) -> CamilloBot:
    """
    mode:
      "dry"   → DryRunBroker (no real orders, safe for testing)
      "paper" → Alpaca paper account
      "live"  → Alpaca live account (requires CAMILLO_LIVE_CONFIRM env var)
    """
    if config is None:
        config = Config.from_env()

    if mode == "dry":
        broker = DryRunBroker()
        log.info("Running in DRY RUN mode — no real orders will be placed")
    elif mode == "paper":
        config.validate()
        broker = AlpacaBroker(config.alpaca_api_key, config.alpaca_secret_key, paper=True)
        log.info("Running in PAPER TRADING mode")
    elif mode == "live":
        config.paper_trading = False
        config.validate()
        broker = AlpacaBroker(config.alpaca_api_key, config.alpaca_secret_key, paper=False)
        log.warning("Running in LIVE TRADING mode — real money at risk")
    else:
        raise ValueError(f"Unknown mode: {mode!r}. Use 'dry', 'paper', or 'live'.")

    db = Database()
    return CamilloBot(broker=broker, config=config, db=db)

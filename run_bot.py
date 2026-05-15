#!/usr/bin/env python3
"""
Camillo Social Arbitrage Bot — entry point

Usage:
  python3 run_bot.py                    # dry run (default, no broker needed)
  python3 run_bot.py --mode paper       # Alpaca paper trading
  python3 run_bot.py --mode live        # Alpaca live (requires confirmation env var)
  python3 run_bot.py --scan-now         # run one scan immediately and exit
  python3 run_bot.py --exit-check-now   # run one exit check immediately and exit
  python3 run_bot.py --status           # print portfolio summary and exit

Required env vars (paper/live modes):
  ALPACA_API_KEY         your Alpaca API key ID
  ALPACA_SECRET_KEY      your Alpaca API secret key
  ALPACA_PAPER           "true" (default) or "false"

Optional env vars:
  REDDIT_CLIENT_ID       for full Reddit buzz scoring
  REDDIT_CLIENT_SECRET
  CAMILLO_LIVE_CONFIRM   must equal "YES_USE_REAL_MONEY" to enable live mode

Get free Alpaca paper trading keys at: https://alpaca.markets
"""

import argparse
import logging
import sys

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("camillo_bot.log"),
    ],
)
log = logging.getLogger("run_bot")


def main():
    parser = argparse.ArgumentParser(description="Camillo Social Arbitrage Bot")
    parser.add_argument(
        "--mode", choices=["dry", "paper", "live"], default="dry",
        help="Broker mode: dry (default), paper, or live",
    )
    parser.add_argument("--scan-now",       action="store_true", help="Run one scan and exit")
    parser.add_argument("--exit-check-now", action="store_true", help="Run one exit check and exit")
    parser.add_argument("--status",         action="store_true", help="Print portfolio summary and exit")
    parser.add_argument("--force",          action="store_true", help="Bypass market-hours check (for testing)")
    args = parser.parse_args()

    from camillo_bot.bot import build_bot

    log.info("Starting Camillo Bot | mode=%s", args.mode)
    try:
        bot = build_bot(mode=args.mode)
    except Exception as e:
        log.error("Failed to initialize bot: %s", e)
        if "unauthorized" in str(e).lower() or "401" in str(e):
            print("\n  ✗ Alpaca credentials rejected (401 Unauthorized)")
            print("    → Get your free paper trading keys at: https://alpaca.markets")
            print("    → Then: export ALPACA_API_KEY=... ALPACA_SECRET_KEY=...")
        sys.exit(1)

    try:
        if args.scan_now:
            log.info("Running scan now (one-shot)...")
            bot.run_scan(force=args.force)
        elif args.exit_check_now:
            log.info("Running exit check now (one-shot)...")
            bot.run_exit_check(force=args.force)
        elif args.status:
            bot.executor.print_portfolio_summary()
        else:
            log.info("Entering scheduler loop... (Ctrl+C to stop)")
            try:
                bot.start()
            except KeyboardInterrupt:
                log.info("Bot stopped by user")
                bot.run_daily_summary()
    except Exception as e:
        if "unauthorized" in str(e).lower() or "401" in str(e):
            log.error("Alpaca API rejected credentials: %s", e)
            print("\n  ✗ Alpaca credentials rejected (401 Unauthorized)")
            print("    → Get your free paper trading keys at: https://alpaca.markets")
            print("    → Then: export ALPACA_API_KEY=<id> ALPACA_SECRET_KEY=<secret>")
            sys.exit(1)
        raise


if __name__ == "__main__":
    main()

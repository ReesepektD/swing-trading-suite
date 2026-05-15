"""
QQQ Bot Scheduler
=================
Runs on a loop and fires the bot at:
  08:00 ET → pre-market email
  12:00 ET → mid-day email
  16:15 ET → after-close signal run (bot execution)

Start it once and leave it running:
  python3 scheduler.py

Or use cron (more reliable for daily use):
  crontab -e
  0  8  * * 1-5  EMAIL_FROM=... EMAIL_USER=... EMAIL_PASS=... ALPACA_KEY=... ALPACA_SECRET=... /usr/bin/python3 /path/to/trading_bot.py --premarket
  0 12  * * 1-5  EMAIL_FROM=... EMAIL_USER=... EMAIL_PASS=... ALPACA_KEY=... ALPACA_SECRET=... /usr/bin/python3 /path/to/trading_bot.py --midday
  15 16 * * 1-5  ALPACA_KEY=... ALPACA_SECRET=... /usr/bin/python3 /path/to/trading_bot.py --run
"""

import time
import logging
import subprocess
import sys
from datetime import datetime
import pytz

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scheduler")

ET        = pytz.timezone("America/New_York")
BOT_PATH  = __file__.replace("scheduler.py", "trading_bot.py")
PYTHON    = sys.executable

# ── Schedule (ET hour, minute) → mode ────────────────────────────────────────
SCHEDULE = [
    (8,  0,  "--premarket"),
    (12, 0,  "--midday"),
    (16, 15, "--run"),
]

fired_today: set[str] = set()


def run_bot(mode: str) -> None:
    log.info(f"Firing: python3 trading_bot.py {mode}")
    result = subprocess.run(
        [PYTHON, BOT_PATH, mode],
        capture_output=False,
        env=None,          # inherits all env vars (EMAIL_*, ALPACA_*)
    )
    if result.returncode != 0:
        log.error(f"Bot exited with code {result.returncode}")


def main() -> None:
    log.info("Scheduler started. Watching for 8:00, 12:00, 16:15 ET on weekdays.")
    global fired_today

    while True:
        now    = datetime.now(ET)
        today  = now.strftime("%Y-%m-%d")
        is_wkd = now.weekday() >= 5          # Saturday=5, Sunday=6

        # Reset fired set at midnight
        if now.hour == 0 and now.minute == 0:
            fired_today = set()

        if not is_wkd:
            for (h, m, mode) in SCHEDULE:
                key = f"{today}-{mode}"
                if now.hour == h and now.minute == m and key not in fired_today:
                    fired_today.add(key)
                    run_bot(mode)

        time.sleep(30)   # check every 30 seconds


if __name__ == "__main__":
    main()

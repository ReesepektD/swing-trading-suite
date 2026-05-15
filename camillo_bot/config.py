"""
Central configuration for the Camillo Social Arbitrage Bot.
All secrets come from environment variables — never hardcode credentials.
"""

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    # ── Broker ────────────────────────────────────────────────────────
    alpaca_api_key:    str  = ""
    alpaca_secret_key: str  = ""
    paper_trading:     bool = True   # must be explicitly set False for live

    # ── Strategy thresholds ───────────────────────────────────────────
    min_buy_score:    float = 70.0   # composite score to trigger a BUY order
    min_watch_score:  float = 50.0   # score to keep on radar (no order)
    exit_score_floor: float = 40.0   # close position if re-scan score drops here

    # ── Position sizing (Camillo's tiered conviction model) ───────────
    buy_position_pct:   float = 0.08  # up to 8% of portfolio equity per BUY
    watch_position_pct: float = 0.04  # up to 4% for WATCH (if manually approved)
    max_positions:      int   = 12    # max concurrent open positions

    # ── Risk management ───────────────────────────────────────────────
    stop_loss_pct:        float = 0.15  # hard stop: sell if down 15% from entry
    take_profit_half_pct: float = 1.00  # take 50% off the table if up 100%
    max_hold_weeks:       int   = 26    # Camillo's maximum hold duration
    max_drawdown_halt:    float = 0.20  # pause all new entries if portfolio -20%

    # ── Trend re-check interval ───────────────────────────────────────
    trend_recheck_days: int = 7   # how often to re-run Google Trends per position
    trend_exit_weeks:   int = 2   # close if trend slope negative for this many weeks

    # ── Scheduling (market timezone — Eastern) ────────────────────────
    scan_time:        str  = "09:45"
    exit_check_times: list = field(default_factory=lambda: ["12:00", "15:30"])

    # ── Reddit (optional enrichment) ──────────────────────────────────
    reddit_client_id:     str = ""
    reddit_client_secret: str = ""

    # ── Watchlist ─────────────────────────────────────────────────────
    # Each entry: {"ticker": "XYZ", "keywords": ["plain english search terms"]}
    # Keywords should be what a non-investor would type — not the company name.
    watchlist: list = field(default_factory=lambda: [
        {"ticker": "CELH",  "keywords": ["celsius drink", "celsius energy drink"]},
        {"ticker": "ONON",  "keywords": ["on running shoes", "on cloud shoes"]},
        {"ticker": "BIRK",  "keywords": ["birkenstock", "birkenstock sandals"]},
        {"ticker": "DUOL",  "keywords": ["duolingo", "duolingo streak"]},
        {"ticker": "CAVA",  "keywords": ["cava restaurant", "cava bowl", "cava mediterranean"]},
        {"ticker": "BROS",  "keywords": ["dutch bros coffee", "dutch brothers coffee"]},
    ])

    @classmethod
    def from_env(cls) -> "Config":
        """Load a Config with credentials pulled from environment variables."""
        return cls(
            alpaca_api_key       = os.getenv("ALPACA_API_KEY", ""),
            alpaca_secret_key    = os.getenv("ALPACA_SECRET_KEY", ""),
            paper_trading        = os.getenv("ALPACA_PAPER", "true").lower() != "false",
            reddit_client_id     = os.getenv("REDDIT_CLIENT_ID", ""),
            reddit_client_secret = os.getenv("REDDIT_CLIENT_SECRET", ""),
        )

    def validate(self):
        """Raise if any required fields are missing."""
        if not self.alpaca_api_key or not self.alpaca_secret_key:
            raise ValueError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set.\n"
                "Get free paper trading keys at: https://alpaca.markets"
            )
        if not self.paper_trading:
            # Require an explicit second confirmation for live trading
            confirm = os.getenv("CAMILLO_LIVE_CONFIRM", "")
            if confirm != "YES_USE_REAL_MONEY":
                raise ValueError(
                    "Live trading requires CAMILLO_LIVE_CONFIRM=YES_USE_REAL_MONEY"
                )

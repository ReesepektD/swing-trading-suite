"""
Central configuration for the Camillo Social Arbitrage Bot.
All secrets come from environment variables — never hardcode credentials.
"""

import os
import pathlib
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
    # Left empty — populated dynamically each morning by MarketScanner.
    # Override with a static list for backtesting or targeted manual scans.
    watchlist: list = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Config":
        """Load a Config with credentials pulled from environment variables.

        Resolution order (first complete source wins):
          1. ALPACA_API_KEY + ALPACA_SECRET_KEY  (set by `alpaca camillo` or user)
          2. Alpaca CLI profile YAML (~/.config/alpaca/profiles/<name>.yaml)
          3. Empty — caller should call validate() before trading
        """
        api_key    = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")

        # Derive paper flag: env var > profile live_trade field > default paper
        live_trade_env = os.getenv("ALPACA_LIVE_TRADE", "").strip().lower()
        if live_trade_env == "true":
            paper = False
        elif live_trade_env in ("false", ""):
            paper = True  # re-evaluated below from profile if no keys yet
        else:
            paper = True

        if not (api_key and secret_key):
            # Attempt to read from the Alpaca CLI profile store
            profile_creds = _load_alpaca_cli_profile()
            if profile_creds:
                api_key    = profile_creds.get("api_key", "")
                secret_key = profile_creds.get("secret_key", "")
                # Honour the profile's live_trade field only when env didn't say
                if live_trade_env == "":
                    paper = not bool(profile_creds.get("live_trade", False))

        return cls(
            alpaca_api_key       = api_key,
            alpaca_secret_key    = secret_key,
            paper_trading        = paper,
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
            confirm = os.getenv("CAMILLO_LIVE_CONFIRM", "")
            if confirm != "YES_USE_REAL_MONEY":
                raise ValueError(
                    "Live trading requires CAMILLO_LIVE_CONFIRM=YES_USE_REAL_MONEY"
                )


def _load_alpaca_cli_profile() -> dict:
    """Read credentials from the Alpaca CLI profile store (YAML format).

    Checks, in order:
      1. ALPACA_CONFIG_DIR env var  (matches Go config.Dir())
      2. ~/.config/alpaca/

    The active profile name comes from ALPACA_PROFILE, then config.yaml's
    default_profile, then falls back to "paper".
    Returns a dict with keys api_key, secret_key, live_trade (bool) or {}.
    """
    try:
        import yaml  # PyYAML — optional; silently skip if not installed
    except ImportError:
        return {}

    config_dir_env = os.getenv("ALPACA_CONFIG_DIR", "")
    if config_dir_env:
        config_dir = pathlib.Path(config_dir_env)
    else:
        config_dir = pathlib.Path.home() / ".config" / "alpaca"

    # Resolve profile name the same way the Go CLI does
    profile_name = os.getenv("ALPACA_PROFILE", "")
    if not profile_name:
        global_cfg_path = config_dir / "config.yaml"
        if global_cfg_path.exists():
            try:
                with global_cfg_path.open() as f:
                    global_cfg = yaml.safe_load(f) or {}
                profile_name = global_cfg.get("default_profile", "")
            except Exception:
                pass
    if not profile_name:
        profile_name = "paper"

    profile_path = config_dir / "profiles" / f"{profile_name}.yaml"
    if not profile_path.exists():
        return {}

    try:
        with profile_path.open() as f:
            data = yaml.safe_load(f) or {}
        return {
            "api_key":    data.get("api_key", ""),
            "secret_key": data.get("secret_key", ""),
            "live_trade": bool(data.get("live_trade", False)),
        }
    except Exception:
        return {}

"""
Markov Regime Scanner — trading suite integration.

Wraps the markov-hedge-fund-method skill's regime detection into the
ArbitrageSignal interface so it plugs directly into CamilloBot's
entry/exit pipeline.

Framework: Roan (@RohOnChain). Skill: Lewis Jackson (@jackson-video-resources).

Standalone:
    python3 markov_regime.py --ticker QQQ
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from camillo_bot.yahoo_data import get_client

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regime core (self-contained, no dependency on skill install location)
# ---------------------------------------------------------------------------

STATES = ["Bear", "Sideways", "Bull"]  # index 0, 1, 2


def _label_regimes(close: pd.Series, window: int = 20, threshold: float = 0.02) -> pd.Series:
    rolling_return = close.pct_change(window)
    labels = pd.Series(1, index=close.index, dtype=int)
    labels[rolling_return > threshold] = 2
    labels[rolling_return < -threshold] = 0
    return labels.dropna()


def _build_transition_matrix(labels: pd.Series) -> np.ndarray:
    n = 3
    counts = np.zeros((n, n), dtype=float)
    arr = labels.to_numpy()
    for i in range(len(arr) - 1):
        counts[arr[i], arr[i + 1]] += 1
    row_sums = counts.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    return counts / row_sums


def _stationary_distribution(P: np.ndarray) -> np.ndarray:
    eigvals, eigvecs = np.linalg.eig(P.T)
    idx = np.argmin(np.abs(eigvals - 1.0))
    vec = np.real(eigvecs[:, idx])
    vec = np.abs(vec)
    return vec / vec.sum()


def _signal_score(P: np.ndarray, current_state: int) -> float:
    """bull_prob − bear_prob for the current state, mapped to 0–100."""
    raw = float(P[current_state, 2] - P[current_state, 0])
    return float(np.clip((raw + 1.0) / 2.0 * 100, 0, 100))


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------

@dataclass
class RegimeSignal:
    ticker:          str
    regime:          str          # "Bull", "Bear", or "Sideways"
    bull_prob:       float        # P(next = Bull | current)
    bear_prob:       float        # P(next = Bear | current)
    sideways_prob:   float        # P(next = Sideways | current)
    stationary:      dict         # long-run mix {"Bull": %, "Bear": %, "Sideways": %}
    composite_score: float        # 0–100 → maps to BUY/WATCH/PASS
    signal:          str          # "BUY" / "WATCH" / "PASS"
    keywords:        list = field(default_factory=list)
    notes:           list = field(default_factory=list)

    @property
    def trend_velocity_score(self) -> float:
        return self.composite_score

    @property
    def analyst_gap_score(self) -> float:
        return 0.0

    @property
    def reddit_buzz_score(self) -> float:
        return 0.0

    @property
    def price_lag_score(self) -> float:
        return 0.0


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class MarkovHedgeScanner:
    """
    Scores tickers by their current Markov regime and the probability of
    transitioning into a Bull regime next session.

    Composite score = bull_prob − bear_prob, mapped to 0–100.
    Thresholds:
        BUY   ≥ 65  (bull regime, strong forward probability)
        WATCH 45–64
        PASS  < 45
    """

    def __init__(self, window: int = 20, threshold: float = 0.02, history_period: str = "2y"):
        self.window = window
        self.threshold = threshold
        self.history_period = history_period

    def score_ticker(self, ticker: str, keywords: list | None = None) -> RegimeSignal:
        keywords = keywords or [ticker.lower()]
        try:
            hist = get_client().get_history(ticker, period=self.history_period)
            if hist.empty:
                raise ValueError(f"No history returned for {ticker}")

            close = hist["Close"].dropna()
            labels = _label_regimes(close, window=self.window, threshold=self.threshold)
            P = _build_transition_matrix(labels)
            pi = _stationary_distribution(P)

            current_state = int(labels.iloc[-1])
            regime = STATES[current_state]
            bull_p = float(P[current_state, 2])
            bear_p = float(P[current_state, 0])
            side_p = float(P[current_state, 1])
            score  = _signal_score(P, current_state)

            if   score >= 65: sig = "BUY"
            elif score >= 45: sig = "WATCH"
            else:             sig = "PASS"

            notes = [
                f"Regime: {regime}",
                f"→Bull {bull_p*100:.1f}% | →Bear {bear_p*100:.1f}% | →Sideways {side_p*100:.1f}%",
                f"Stationary: Bull {pi[2]*100:.0f}% Bear {pi[0]*100:.0f}% Sideways {pi[1]*100:.0f}%",
            ]
            log.info("Markov %s | regime=%s score=%.1f sig=%s", ticker, regime, score, sig)
            return RegimeSignal(
                ticker          = ticker,
                regime          = regime,
                bull_prob       = bull_p,
                bear_prob       = bear_p,
                sideways_prob   = side_p,
                stationary      = {"Bull": pi[2], "Bear": pi[0], "Sideways": pi[1]},
                composite_score = round(score, 1),
                signal          = sig,
                keywords        = keywords,
                notes           = notes,
            )

        except Exception as exc:
            log.warning("Markov score failed for %s: %s", ticker, exc)
            return RegimeSignal(
                ticker          = ticker,
                regime          = "Unknown",
                bull_prob       = 0.0,
                bear_prob       = 0.0,
                sideways_prob   = 0.0,
                stationary      = {},
                composite_score = 50.0,
                signal          = "PASS",
                keywords        = keywords,
                notes           = [f"error: {exc}"],
            )

    def scan_watchlist(self, watchlist: list) -> pd.DataFrame:
        rows = []
        for item in watchlist:
            sig = self.score_ticker(item["ticker"], item.get("keywords"))
            rows.append({
                "Ticker":   sig.ticker,
                "Signal":   sig.signal,
                "Score":    sig.composite_score,
                "Regime":   sig.regime,
                "→Bull":    f"{sig.bull_prob*100:.1f}%",
                "→Bear":    f"{sig.bear_prob*100:.1f}%",
                "Notes":    " | ".join(sig.notes),
            })
        if not rows:
            return pd.DataFrame()
        return (pd.DataFrame(rows)
                  .sort_values("Score", ascending=False)
                  .reset_index(drop=True))


# ---------------------------------------------------------------------------
# CLI — standalone usage
# ---------------------------------------------------------------------------

def _print_results(df: pd.DataFrame) -> None:
    icons = {"BUY": "●", "WATCH": "◐", "PASS": "○"}
    print("\n" + "═" * 68)
    print("  MARKOV REGIME SCAN RESULTS")
    print("═" * 68)
    for _, row in df.iterrows():
        icon = icons.get(row["Signal"], "?")
        print(f"\n  {icon} {row['Ticker']:<6}  [{row['Signal']}]  "
              f"Score: {row['Score']:.1f}/100  Regime: {row['Regime']}")
        print(f"     {row['Notes']}")
    print("\n" + "═" * 68 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Markov regime scanner")
    parser.add_argument("--ticker",  default="QQQ", help="Single ticker to score")
    parser.add_argument("--window",  type=int, default=20)
    parser.add_argument("--years",   type=int, default=2,
                        help="History in years (passed as Ny to yfinance)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    scanner = MarkovHedgeScanner(
        window=args.window,
        history_period=f"{args.years}y",
    )

    from camillo_bot.config import Config
    cfg = Config.from_env()
    watchlist = cfg.watchlist if args.ticker.upper() == "ALL" else [
        {"ticker": args.ticker.upper(), "keywords": [args.ticker.lower()]}
    ]

    results = scanner.scan_watchlist(watchlist)
    if results.empty:
        print("No results.")
        return 1

    _print_results(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())

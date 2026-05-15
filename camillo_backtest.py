
"""
Chris Camillo Social Arbitrage Backtest Framework
Uses Google Trends as a proxy for social momentum signals.
"""

import time
import warnings
import numpy as np
import pandas as pd
import yfinance as yf
from pytrends.request import TrendReq

warnings.filterwarnings("ignore")


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_price_data(ticker: str, start: str, end: str) -> pd.DataFrame:
    df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


def fetch_trends_data(keyword: str, start: str, end: str, retries: int = 3) -> pd.Series:
    pytrends = TrendReq(hl="en-US", tz=360)
    all_chunks = []
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)

    # pytrends weekly data max window ~5 years; chunk by year to stay safe
    chunk_start = s
    while chunk_start < e:
        chunk_end = min(chunk_start + pd.DateOffset(years=1), e)
        timeframe = f"{chunk_start.date()} {chunk_end.date()}"
        for attempt in range(retries):
            try:
                pytrends.build_payload([keyword], timeframe=timeframe)
                df = pytrends.interest_over_time()
                if not df.empty and keyword in df.columns:
                    all_chunks.append(df[keyword])
                break
            except Exception:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        chunk_start = chunk_end + pd.DateOffset(days=1)
        time.sleep(1)

    if not all_chunks:
        return pd.Series(dtype=float)

    combined = pd.concat(all_chunks)
    combined = combined[~combined.index.duplicated(keep="last")]
    combined.index = pd.to_datetime(combined.index).tz_localize(None)
    return combined.sort_index().astype(float)


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def compute_trend_slope(series: pd.Series, window: int = 4) -> pd.Series:
    """Rolling linear slope over `window` periods (normalised by mean)."""
    slopes = pd.Series(index=series.index, dtype=float)
    vals = series.values
    for i in range(window - 1, len(vals)):
        y = vals[i - window + 1 : i + 1]
        if np.isnan(y).any():
            continue
        x = np.arange(window, dtype=float)
        slope = np.polyfit(x, y, 1)[0]
        mean_val = np.mean(y) if np.mean(y) != 0 else 1.0
        slopes.iloc[i] = slope / mean_val
    return slopes


def compute_price_momentum(price: pd.Series, window: int = 4) -> pd.Series:
    """4-week price return as momentum proxy (weekly resampled)."""
    weekly = price.resample("W").last().ffill()
    mom = weekly.pct_change(window)
    return mom


def align_signals(price_df: pd.DataFrame, trends: pd.Series) -> pd.DataFrame:
    """Align weekly trend signal with daily price data."""
    weekly_close = price_df["Close"].resample("W").last().ffill()
    trend_slope = compute_trend_slope(trends, window=4)
    price_mom = compute_price_momentum(price_df["Close"], window=4)

    combined = pd.DataFrame({
        "trend": trends,
        "trend_slope": trend_slope,
        "price_mom": price_mom,
        "weekly_close": weekly_close,
    }).dropna()

    # Expand back to daily for easier trade management
    daily = price_df[["Close", "Open"]].copy()
    combined_daily = combined.reindex(daily.index, method="ffill")
    combined_daily["Close"] = daily["Close"]
    combined_daily["Open"] = daily["Open"]
    return combined_daily.dropna()


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------

def run_backtest(
    ticker: str,
    keyword: str,
    start: str,
    end: str,
    max_hold_weeks: int = 26,
    plateau_weeks: int = 2,
    initial_capital: float = 10_000.0,
) -> dict:
    price_df = fetch_price_data(ticker, start, end)
    if price_df.empty:
        return {"ticker": ticker, "error": "No price data"}

    trends = fetch_trends_data(keyword, start, end)
    if trends.empty:
        return {"ticker": ticker, "error": "No trends data"}

    signals = align_signals(price_df, trends)
    if signals.empty:
        return {"ticker": ticker, "error": "Signal alignment failed"}

    trades = []
    in_trade = False
    entry_date = None
    entry_price = None
    neg_slope_streak = 0
    capital = initial_capital
    equity_curve = [initial_capital]
    equity_dates = [signals.index[0]]

    dates = signals.index.tolist()

    for i, date in enumerate(dates):
        row = signals.loc[date]

        if in_trade:
            hold_weeks = (date - entry_date).days / 7
            slope = row["trend_slope"]
            neg_slope_streak = neg_slope_streak + 1 if slope < 0 else 0

            exit_signal = (neg_slope_streak >= plateau_weeks) or (hold_weeks >= max_hold_weeks)

            if exit_signal:
                exit_price = row["Open"] if not pd.isna(row["Open"]) else row["Close"]
                pnl_pct = (exit_price - entry_price) / entry_price
                capital *= 1 + pnl_pct
                trades.append({
                    "entry_date": entry_date,
                    "exit_date": date,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl_pct": pnl_pct,
                    "hold_days": (date - entry_date).days,
                    "exit_reason": "plateau" if neg_slope_streak >= plateau_weeks else "max_hold",
                })
                in_trade = False
                neg_slope_streak = 0

        else:
            slope = row["trend_slope"]
            price_mom = row["price_mom"]

            entry_signal = (
                slope > 0
                and not pd.isna(price_mom)
                and price_mom < slope  # price hasn't caught up to trend momentum
                and row["trend"] > 10  # filter noise (Google Trends 0-100)
            )

            if entry_signal:
                entry_price = row["Open"] if not pd.isna(row["Open"]) else row["Close"]
                if pd.isna(entry_price) or entry_price <= 0:
                    continue
                entry_date = date
                in_trade = True
                neg_slope_streak = 0

        equity_curve.append(capital)
        equity_dates.append(date)

    # Force-close any open position at last available price
    if in_trade and entry_price is not None:
        last_row = signals.iloc[-1]
        exit_price = last_row["Close"]
        pnl_pct = (exit_price - entry_price) / entry_price
        capital *= 1 + pnl_pct
        trades.append({
            "entry_date": entry_date,
            "exit_date": signals.index[-1],
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl_pct": pnl_pct,
            "hold_days": (signals.index[-1] - entry_date).days,
            "exit_reason": "end_of_data",
        })

    equity_series = pd.Series(equity_curve, index=equity_dates)
    metrics = compute_metrics(ticker, trades, equity_series, initial_capital, start, end)
    return metrics


# ---------------------------------------------------------------------------
# Performance metrics
# ---------------------------------------------------------------------------

def compute_metrics(
    ticker: str,
    trades: list,
    equity: pd.Series,
    initial_capital: float,
    start: str,
    end: str,
) -> dict:
    if not trades:
        return {
            "ticker": ticker, "n_trades": 0, "total_return": 0.0,
            "ann_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
            "win_rate": 0.0, "avg_hold_days": 0.0,
        }

    total_return = (equity.iloc[-1] - initial_capital) / initial_capital

    years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25
    ann_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0

    daily_returns = equity.pct_change().dropna()
    sharpe = 0.0
    if daily_returns.std() > 0:
        # annualise assuming ~252 trading days
        sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)

    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    max_drawdown = drawdown.min()

    pnls = [t["pnl_pct"] for t in trades]
    win_rate = sum(1 for p in pnls if p > 0) / len(pnls)
    avg_hold = np.mean([t["hold_days"] for t in trades])

    return {
        "ticker": ticker,
        "n_trades": len(trades),
        "total_return": total_return,
        "ann_return": ann_return,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "avg_hold_days": avg_hold,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_summary(results: list[dict]) -> None:
    col_widths = {
        "Ticker":        8,
        "Trades":        7,
        "Total Ret":    10,
        "Ann Ret":       9,
        "Sharpe":        8,
        "Max DD":        9,
        "Win Rate":      9,
        "Avg Hold":      9,
    }
    headers = list(col_widths.keys())
    sep = "+" + "+".join("-" * (w + 2) for w in col_widths.values()) + "+"
    header_row = "|" + "|".join(
        f" {h:<{col_widths[h]}} " for h in headers
    ) + "|"

    print("\n" + "=" * len(sep))
    print("  CAMILLO SOCIAL ARBITRAGE BACKTEST  |  2020-2024")
    print("=" * len(sep))
    print(sep)
    print(header_row)
    print(sep)

    for r in results:
        if "error" in r:
            row = f"| {r['ticker']:<8} | {'ERROR: ' + r['error']:<{sum(col_widths.values()) + 2 * (len(col_widths) - 1)}} |"
            print(row)
            print(sep)
            continue

        def fmt_pct(v):
            return f"{v * 100:+.1f}%"

        def fmt_f(v):
            return f"{v:.2f}"

        cells = [
            f"{r['ticker']:<{col_widths['Ticker']}}",
            f"{r['n_trades']:>{col_widths['Trades']}}",
            f"{fmt_pct(r['total_return']):>{col_widths['Total Ret']}}",
            f"{fmt_pct(r['ann_return']):>{col_widths['Ann Ret']}}",
            f"{fmt_f(r['sharpe']):>{col_widths['Sharpe']}}",
            f"{fmt_pct(r['max_drawdown']):>{col_widths['Max DD']}}",
            f"{fmt_pct(r['win_rate']):>{col_widths['Win Rate']}}",
            f"{r['avg_hold_days']:.0f}d".rjust(col_widths['Avg Hold']),
        ]
        print("|" + "|".join(f" {c} " for c in cells) + "|")
        print(sep)

    valid = [r for r in results if "error" not in r and r["n_trades"] > 0]
    if valid:
        print()
        avg_ann = np.mean([r["ann_return"] for r in valid])
        avg_sharpe = np.mean([r["sharpe"] for r in valid])
        avg_dd = np.mean([r["max_drawdown"] for r in valid])
        avg_wr = np.mean([r["win_rate"] for r in valid])
        total_trades = sum(r["n_trades"] for r in valid)
        print(f"  Portfolio avg annualised return : {avg_ann * 100:+.1f}%")
        print(f"  Portfolio avg Sharpe            : {avg_sharpe:.2f}")
        print(f"  Portfolio avg max drawdown      : {avg_dd * 100:.1f}%")
        print(f"  Portfolio avg win rate          : {avg_wr * 100:.1f}%")
        print(f"  Total trades across all tickers : {total_trades}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

TICKER_KEYWORDS = {
    "CELH": "Celsius drink",
    "ONON": "On Running shoes",
    "DUOL": "Duolingo",
    "CROX": "Crocs shoes",
    "LULU": "Lululemon",
}

START = "2020-01-01"
END   = "2024-12-31"


def main():
    print(f"\nFetching data for {len(TICKER_KEYWORDS)} tickers ({START} → {END})...")
    print("(Google Trends API calls may be slow — please wait)\n")

    results = []
    for ticker, keyword in TICKER_KEYWORDS.items():
        print(f"  Backtesting {ticker} [{keyword}]...", end=" ", flush=True)
        result = run_backtest(
            ticker=ticker,
            keyword=keyword,
            start=START,
            end=END,
        )
        status = f"{result['n_trades']} trades" if "error" not in result else result["error"]
        print(status)
        results.append(result)
        time.sleep(2)  # polite pause between pytrends requests

    print_summary(results)


if __name__ == "__main__":
    main()

# QQQ Swing Trading Suite

A TradingView Pine Script indicator combining the strategies of the most successful swing traders,
applied to QQQ (Invesco QQQ Trust — Nasdaq-100 ETF).

## Files

| File | Description |
|------|-------------|
| `QQQ_SwingSuite.pine` | TradingView Pine Script v5 — paste into Pine Editor and add to chart |
| `INVESTING_RULES.md` | Full rulebook with entry checklists, sizing, stops, and exit rules |

## Traders & Strategies Included

| Trader | Strategy | What's in the Suite |
|--------|----------|-------------------|
| Mark Minervini | SEPA / VCP | Trend Template (7-condition score), Volatility Contraction Pattern detection |
| William O'Neil | CAN SLIM | Volume surge filter, momentum zone RSI, breakout entries, distribution day count |
| Stan Weinstein | Stage Analysis | 4-stage background shading, Stage 2 filter gates all entries |
| Nicolas Darvas | Box Method | Darvas box plotted, breakout label on volume confirmation |
| Larry Williams | %R / Momentum | RSI momentum zone, MACD filter, trend strength |
| Jesse Livermore | Pivotal Points | Breakout-only entries (never buy weakness), stop discipline |

## What the Suite Shows

- **EMA 21 / 50** and **SMA 150 / 200** — the core trend structure
- **Darvas Box** — dynamic support/resistance box
- **VCP Band** — highlights when volatility has contracted ≥30% (high-probability setup window)
- **Stage shading** — green background = Stage 2 bull, red = Stage 4 bear
- **Entry labels** — ENTRY (Momentum / Darvas BO / VCP) and ADD (Pullback)
- **Exit labels** — triggered by EMA50 break on volume or Stage 4 onset
- **Info table** (top-right) — real-time dashboard: Trend Template score, Stage, VCP, MACD, RSI, Volume, Stop, Target

## How to Install

1. Go to [TradingView](https://tradingview.com) → open a chart → set symbol to `QQQ`, timeframe to `1D`
2. Open **Pine Editor** (bottom of screen)
3. Paste the entire contents of `QQQ_SwingSuite.pine`
4. Click **Add to chart**
5. Configure alerts using the built-in `alertcondition` triggers (Momentum Entry, Darvas BO, VCP, Pullback, Exit)

## Core Trading Philosophy

> Only trade QQQ long when it is in **Stage 2** with a **Trend Template score ≥ 6/7**.
> Enter on high-volume breakouts from VCP or Darvas Box setups.
> Cut losses at **2× ATR**. Take first profits at **3× ATR**.
> Never average down. Let winners run to the EMA21 trailing stop.

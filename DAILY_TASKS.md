# QQQ Swing Trading — Daily Task List

---

## Pre-Market (8:00–9:30 AM ET)

```
□ Check VIX level
      < 20  → full size allowed
      20–30 → reduce all position sizes 50%
      > 30  → no new entries; manage existing only
      > 40  → exit to cash

□ Scan macro calendar (FOMC, CPI, Jobs, GDP releases today?)
      Major release day → no new entries until 30 min after report

□ Check if QQQ gapped up or down overnight
      Gap up > 3% + extended move → watch for climax run exit signal
      Gap down > 2% on volume   → check if EMA50 is threatened

□ Review open positions
      Is each position still above its stop (2× ATR)?
      Is each position still above EMA21?
      Adjust stop to breakeven on any position up ≥ 10%
```

---

## Market Open (9:30–10:00 AM ET)

```
□ Let the first 15–30 min settle — do NOT trade the open bell

□ Note QQQ's opening range (first 15-min high and low)
      This becomes the intraday support/resistance reference

□ Watch volume pace vs 20-day average
      Heavy early volume = potential trend day
      Light early volume = likely choppy/mean-revert day
```

---

## Mid-Day Check (12:00–1:00 PM ET)

```
□ Is QQQ holding above EMA21 on the daily?
□ Any open position down > 5% intraday? → review stop

□ Distribution check (O'Neil rule):
      Is today a down day on pace for above-average volume?
      Running count this month: __ / 5  (5 = exit all)
```

---

## After Market Close (4:00–5:00 PM ET)  ← most important session

```
□ RUN THE BOT
      cd swing-trading-suite
      ALPACA_KEY=... ALPACA_SECRET=... python3 trading_bot.py

□ Record bot output in trade journal:
      - Trend Template score today: __ / 7
      - Stage: 2-Bull / 4-Bear / 1/3-Neutral
      - MACD: Bull / Bear
      - RSI: ___
      - Vol Surge: Yes / No
      - VCP Tight: Yes / No
      - Any signals fired? (Momentum / Darvas / VCP / Pullback / B&B / T&T)
      - Any exit signals?

□ Open TradingView → QQQ Daily chart (QQQ_SwingSuite or QQQ_BotStrategy)
      Visually confirm bot output matches chart
      Check dashboard table (top-right): score, stage, stop, target

□ Review entry checklist for any signal that fired:
      □ Stage 2 confirmed?
      □ TT score ≥ 6?
      □ Volume ≥ 1.5× avg on the signal candle?
      □ MACD bullish?
      □ RSI in valid zone?
      □ Stop calculated (2× ATR)?
      □ Position size ≤ 1.5% portfolio risk?
      □ VIX < 30?
      → ALL YES = execute next morning at open (first 30 min settle)
      → ANY NO  = skip the trade

□ Update open position stops
      Trail stop to EMA21 if position is up > 15%
      Move to breakeven if position is up ≥ 10%

□ Log distribution day if: QQQ closed down AND volume > 20-day avg
      Monthly tally: __ distribution days
      ≥ 3 in 4 weeks → cut exposure 50%
      ≥ 5 in 4 weeks → exit all, move to cash

□ Record in trade journal (for any trade taken today):
      Symbol / setup type
      Entry price + reason
      Stop price
      Target price
      R:R ratio
      Result (if closed)
      What I learned
```

---

## Weekly (Every Friday After Close)

```
□ Tally distribution days for the past 4 weeks
□ Is QQQ still in Stage 2? (SMA200 slope positive?)
□ Is MACD bullish on the WEEKLY chart?
□ Are all open positions above EMA21?
□ Review the week's journal entries — any repeated mistakes?
□ Reset distribution day count if starting a new 4-week window
□ Check OpEx: is next week the 3rd Friday? (amplified moves — reduce size)
```

---

## Monthly (First Trading Day of Month)

```
□ Review last month's trades: win rate, avg R:R, biggest mistake
□ Are the TT threshold settings in the bot still appropriate?
□ Check Alpaca paper account P&L vs benchmark (QQQ buy-and-hold)
□ Reset distribution day counter for new month
```

---

## Signal → Action Quick Reference

| Bot Output | Action |
|------------|--------|
| No signals | Do nothing. Wait. |
| Entry signal + all checklist YES | Buy next morning after 30-min open settles |
| Entry signal + any checklist NO | Skip trade, note why |
| Exit signal (EMA50 break on vol) | Cut or hedge 50% at open |
| Exit signal (Stage 4) | Exit all at open |
| RSI > 75 + gap up > 3% | Take 1/3 profits (climax run warning) |
| Position up 10% | Move stop to breakeven |
| Position up 15%+ | Trail stop to EMA21 |
| 3 consecutive losses | Stop trading 2 days. Re-read rules. |

---

## Bot Command (copy-paste ready)

```bash
cd /Users/korylernout/Claude/swing-trading-suite
ALPACA_KEY=your_key ALPACA_SECRET=your_secret python3 trading_bot.py
```

---

*Tied to: INVESTING_RULES.md · QQQ_SwingSuite.pine · QQQ_BotStrategy.pine · trading_bot.py*

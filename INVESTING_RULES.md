# QQQ Swing Trading Rules

Sources: Mark Minervini (SEPA / VCP), William O'Neil (CAN SLIM), Stan Weinstein (Stage Analysis),
Nicolas Darvas (Box Method), Larry Williams (%R / momentum), Jesse Livermore (pivotal points).

---

## 1. The Non-Negotiable Prerequisite — Stage 2 Only

> *"The most important thing is to be in the right stage." — Stan Weinstein*

- **Never trade long unless QQQ is in Stage 2** (price above a rising SMA200).
- Stage 2 = price > SMA200 **and** SMA200 is rising (higher than it was 10 days ago).
- Stage 4 = cash or hedge. No new longs. Period.
- Stages 1 and 3 = reduced size, no new full positions.

---

## 2. Minervini Trend Template — Minimum Score 6 / 7

All conditions scored before any entry:

| # | Condition | Why |
|---|-----------|-----|
| 1 | Price > SMA200 | Above long-term trend |
| 2 | SMA200 > SMA200 (21 bars ago) | Trend is accelerating up |
| 3 | SMA150 > SMA200 | Medium term above long term |
| 4 | EMA50 > SMA150 and SMA200 | Short term leading |
| 5 | Price > EMA50 | Stock riding the 50 |
| 6 | Price ≥ 30% above 52-week low | Has already broken out of a base |
| 7 | Price ≤ 25% below 52-week high | Not in extended, broken territory |

**Rule:** Score ≥ 6 required for a full position. Score 4–5 = half size max. Score < 4 = no trade.

---

## 3. Entry Setups (in priority order)

### 3A. VCP — Volatility Contraction Pattern (Minervini)
The highest-probability setup. Price forms a series of contracting pullbacks with shrinking volume.

**Checklist:**
- [ ] Trend Template score ≥ 6
- [ ] ATR today < ATR 10 bars ago × 0.70 (tightness confirmed in Pine Suite)
- [ ] At least 2–3 prior pullback legs, each smaller than the last
- [ ] Volume dries up on the contractions
- [ ] Buy the breakout above the pivot high on **above-average volume (≥ 1.5× the 20-day avg)**
- [ ] Pivot is a clearly defined high; not just any intraday spike

### 3B. Darvas Box Breakout (Darvas)
Best for trend continuation after a consolidation box.

**Checklist:**
- [ ] Price has been trading in a clearly defined range for ≥ 3 weeks
- [ ] Box top is tested 2–3 times without breaking
- [ ] Breakout candle closes **above the box top**
- [ ] Volume on breakout day ≥ 1.5× avg
- [ ] Trend Template score ≥ 5
- [ ] Set stop at the box bottom; do not move it up until a new box forms

### 3C. Momentum Breakout (O'Neil / Livermore)
Buy strength, not weakness.

**Checklist:**
- [ ] Price breaks to a new multi-week high on heavy volume
- [ ] MACD line above signal, both above zero
- [ ] RSI 55–70 (momentum zone; not yet overbought)
- [ ] Trend Template score ≥ 6
- [ ] Enter within 5% of the breakout pivot; do not chase extended moves (>8% past pivot = pass)

### 3D. Pullback Re-Entry (O'Neil / Williams)
The first pullback to a key moving average in an established uptrend.

**Checklist:**
- [ ] Trend Template score = 7 (full uptrend only)
- [ ] Stage 2 confirmed
- [ ] Price pulls back to EMA21 or EMA50 on **below-average** volume (shakeout, not distribution)
- [ ] RSI pulls back to 40–55 range without breaking below 40
- [ ] MACD still bullish (above signal line)
- [ ] Buy when the next day opens up or closes above the prior day high (confirm bounce)

---

## 4. Position Sizing

> *"The person who doesn't make mistakes is not taking enough risk. The person who makes the same mistakes twice is taking too much." — Livermore (paraphrase)*

| Condition | Max Position Size |
|-----------|------------------|
| Trend Template 7/7, Stage 2, VCP setup | 25% of portfolio |
| Trend Template 6/7, Stage 2 | 15% of portfolio |
| Trend Template 5/7 | 10% of portfolio |
| Stage 1 / 3 / Score < 5 | 0% — no new positions |
| Stage 4 confirmed | Move to cash / hedge |

- Never hold more than **3 full positions simultaneously** (QQQ + 2 others) to avoid over-diversification diluting returns.
- Scale in: buy 50% at the entry signal, add 25% if it extends 3–5% on volume, add final 25% on the first pullback that holds.

---

## 5. Stop-Loss Rules

> *"Cut losses quickly and let profits run." — O'Neil and Livermore*

- **Hard stop:** 2× ATR below entry price (calculated by Pine Suite on the chart).
- **Maximum loss per trade: 7–8%** from entry. If ATR stop is wider, reduce position size to keep the dollar risk ≤ 1.5% of portfolio per trade.
- **Never average down** into a losing position. A losing trade is telling you something.
- **EMA50 break on volume = mandatory review.** If QQQ closes below EMA50 with volume ≥ 1.5× avg, cut or hedge 50% immediately. If it closes below EMA50 two days in a row, exit fully.
- Move stop to **breakeven** once position is up 10%.

---

## 6. Profit-Taking Rules

> *"I never try to catch the top. I sell when the trend breaks." — Weinstein*

- **Initial target:** 3× ATR above entry (shown in Pine Suite).
- **Rule of 3:** Take 1/3 off at 10% gain, 1/3 at 20% gain, let final 1/3 ride with a trailing stop.
- **Trailing stop:** Trail the EMA21 once you're up >15%. Sell on a close below EMA21.
- **Climax run exit:** If QQQ gaps up >3% on massive volume and RSI > 75 after an extended move — take profits into strength. This is exhaustion, not power.
- **Never let a 10%+ winner turn into a loser.** Move stop to entry (breakeven) after the position is up 10%.

---

## 7. Volume Rules (O'Neil — Institutional Footprints)

Volume tells you whether institutions are buying or distributing.

- **Valid entry volume:** ≥ 1.5× the 20-day average.
- **Distribution signal:** 3+ days of price decline on above-average volume within 4 weeks = reduce exposure by 50%.
- **5 distribution days in 4 weeks in QQQ** = exit all positions and move to cash.
- Low-volume breakouts fail at a high rate — skip them entirely.

---

## 8. Market Timing (Macro Filter)

> *"You can be right about a stock and still lose money if you're wrong about the market." — O'Neil*

Before taking any swing trade in QQQ:

1. **Is QQQ in Stage 2?** (See Rule 1.) If not, stop here.
2. **Is the Trend Template score ≥ 5?** If not, reduce to 0 new positions.
3. **Is MACD bullish on the weekly chart?** If MACD is negative on the weekly, only small positions.
4. **VIX check:** VIX > 30 = reduce all position sizes by 50%. VIX > 40 = cash only.
5. **Look for "follow-through day"** (O'Neil): A major index up 1.7%+ on higher volume than the prior day, at least 4 days into a rally attempt = confirms new uptrend. This is the earliest re-entry after a downtrend.

---

## 9. Psychological Rules (Livermore / Minervini)

- **Trade the chart, not the news.** By the time news is public, the price has moved.
- **Paper trade a setup you've never traded before.** Simulate for 4 weeks before going live.
- **Journal every trade:** Entry reason, stop, target, result, and what you learned.
- **After 3 consecutive losses**, stop trading for 2 days. Re-read the rules. Check if the market stage changed.
- **Do not move your stop lower.** If it's hit, it's hit. Respect the rules.
- **Boredom is not a reason to trade.** Only 3–5 high-quality setups per month in QQQ; that is normal.

---

## 10. TradingView Setup Instructions

1. Open TradingView → Chart → Symbol: `QQQ` → Timeframe: **Daily (1D)**
2. Click **Pine Editor** (bottom panel) → paste the contents of `QQQ_SwingSuite.pine`
3. Click **Add to chart**
4. In a second pane below the chart, add:
   - **MACD** (12/26/9) — built into Pine Suite but useful as standalone too
   - **RSI** (14) — for visual reference
   - **Volume** (with 20-period MA overlay)
5. Set **alerts** using the Pine Suite's built-in `alertcondition` signals:
   - "QQQ Momentum Entry"
   - "QQQ Darvas Breakout"
   - "QQQ VCP Entry"
   - "QQQ Pullback Add"
   - "QQQ Exit Signal"
6. The info table in the **top-right** of the chart gives a real-time dashboard of all conditions.

---

## Quick Reference Checklist (Print This)

```
BEFORE EVERY TRADE:
 □ Stage 2? (green background on chart)
 □ Trend Template ≥ 6/7? (check info table)
 □ Setup type: VCP / Darvas / Momentum / Pullback?
 □ Volume ≥ 1.5× avg on breakout candle?
 □ MACD bullish?
 □ RSI in range (50–70 entry, 40–55 pullback)?
 □ Stop set? (2× ATR below entry)
 □ Position size calculated? (max 1.5% portfolio risk)
 □ First profit target set? (3× ATR / 10% gain)
 □ VIX < 30?

EVERY WEEK:
 □ Count distribution days (should be < 3 in 4 weeks)
 □ Is QQQ still in Stage 2?
 □ Are open positions above EMA21?
 □ Journal any trades taken this week
```

---

*Last updated: 2026-05-11 | Strategies: Minervini (SEPA/VCP), O'Neil (CAN SLIM), Weinstein (Stage Analysis), Darvas (Box), Williams (%R/momentum), Livermore (pivotal points)*

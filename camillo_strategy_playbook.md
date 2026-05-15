# Chris Camillo — Social Arbitrage Strategy Playbook

## Core Thesis

Markets are efficient at processing *financial* information but systematically slow to price *cultural* information. When everyday consumers observe a trend — a new drink, a fashion shift, a viral app — that observation precedes analyst coverage by weeks or months. The gap between street-level knowledge and institutional awareness is the arbitrage.

> "I don't need to know more than Wall Street about finance. I just need to know more about life."
> — Chris Camillo, *Laughing at Wall Street*

---

## The Information Hierarchy

```
EARLY EDGE                                              LATE / NO EDGE
─────────────────────────────────────────────────────────────────────►
Personal      Social       Google      Reddit/      Analyst     CNBC /
observation   circles      Trends      Twitter      reports     Bloomberg
    ↑
 Act here
```

The closer you act to the left side of this hierarchy, the larger the potential return. By the time a trend appears in analyst research, it is mostly priced in.

---

## Universe Construction

### What to look for
- Consumer-facing companies (brand is visible in daily life)
- Companies with <10 analyst ratings (institutional blind spot)
- Small/mid-cap (S&P 500 companies are over-covered)
- Cultural inflection points: fashion, food, fitness, entertainment, tech adoption

### What to avoid
- B2B companies (trends not observable by consumers)
- Commodity businesses (no brand differentiation)
- Stocks already on CNBC / Reddit front page (trend is priced in)
- Companies where you cannot personally verify the trend

---

## Signal Framework

### Factor 1 — Cultural Trend Velocity (30% weight)
**Source:** Google Trends, TikTok, Instagram hashtag growth

**Signal:** 90-day slope of search interest is positive AND accelerating (last 30 days > prior 30 days)

**Green flags:**
- Trend interest up >50% over 90 days
- Acceleration in the last 30 days
- Multiple related keywords all trending together

**Red flags:**
- Single spike (viral moment) without sustained follow-through
- Trend is seasonal / cyclical (not a structural shift)

---

### Factor 2 — Analyst Coverage Gap (25% weight)
**Source:** Yahoo Finance analyst count, Bloomberg consensus

**Signal:** Fewer analysts → larger information gap → more potential upside

| Analyst Count | Coverage Score | Interpretation              |
|:---:          |:---:           |:---                         |
| 0–2           | 90–100         | Institutional blind spot    |
| 3–5           | 75–85          | Emerging awareness          |
| 6–10          | 50–65          | Partially discovered        |
| 11–20         | 25–40          | Well-covered, limited edge  |
| 20+           | 0–15           | Fully priced in, pass       |

**Modifier:** If analyst consensus target is >15% above current price, add +10 to score (analysts bullish but market hasn't moved yet).

---

### Factor 3 — Social Buzz & Sentiment (25% weight)
**Source:** Reddit (r/investing, r/wallstreetbets, r/stocks), Twitter/X, StockTwits

**Signal:** Rising mention velocity with net positive sentiment, but not yet at peak frenzy

**Scoring:**
- Volume: 0–60 points (50+ mentions/week = max)
- Sentiment: 0–40 points (VADER compound score mapped to 0–40)

**Camillo's nuance:** You want the trend on social media, not *financial* social media. A shoe brand trending on TikTok is more valuable than it trending on WallStreetBets. By the time WSB finds it, the edge is shrinking.

---

### Factor 4 — Price Lag (20% weight)
**Source:** yfinance 90-day OHLCV

**Signal:** Social trend slope > price slope (large gap = opportunity)

**The ideal setup:**
```
Google Trends:  ────────────/──────────────  (rising steeply)
Stock Price:    ───────────────────────────  (flat / barely moving)
                                 ↑
                          Buy zone here
```

**Warning:** If price has already moved 30%+ in the direction of the trend, much of the arbitrage is already captured. Recalculate whether the remaining upside justifies the risk.

---

## Composite Score & Signal Rules

```
Composite = Trend(0.30) + AnalystGap(0.25) + Reddit(0.25) + PriceLag(0.20)

Score ≥ 70  → BUY    (high-conviction social arb setup)
Score 50–69 → WATCH  (trend forming, wait for confirmation)
Score < 50  → PASS   (trend priced in or not confirmed)
```

---

## Entry Rules

All four conditions should be true before entering:

1. **Composite score ≥ 70**
2. **You personally observed the trend** before running the scanner (do not enter blindly on numbers alone — Camillo's framework requires ground-truth observation)
3. **Stock is not at a 52-week high** (price hasn't already run)
4. **Earnings are not within 2 weeks** (avoid event risk around the entry)

### Entry mechanics
- Market order at open the next trading day after signal confirmed
- Do not chase intraday spikes
- Set a calendar reminder to reassess in 4 weeks

---

## Exit Rules

Exit when any of the following triggers:

| Exit Trigger                             | Action         |
|:---                                      |:---            |
| Google Trends slope turns negative 2 wks  | Full exit      |
| Analyst count doubles from entry          | Reduce 50%     |
| Stock appears in mainstream financial media | Reduce 50%   |
| Your non-investor friends mention it      | Full exit      |
| Position up >100% from entry              | Take 50% off   |
| 26-week max hold reached                  | Full exit      |
| Composite score drops below 40            | Full exit      |

### Exit mechanics
- Sell at close (not open) to avoid morning volatility
- Never exit more than 25% of position in a single hour
- Do not re-enter a position you exited within 30 days

---

## Position Sizing

Camillo uses a tiered conviction model. No single position should cause catastrophic loss if the thesis is wrong.

| Signal  | Max Position Size | Typical Hold |
|:---     |:---               |:---          |
| BUY     | 10% of portfolio  | 4–26 weeks   |
| WATCH   | 5%  of portfolio  | 4–12 weeks   |
| PASS    | 0%                | —            |

**Options (advanced):** Camillo occasionally uses call options for very high-conviction setups (score ≥ 85). Use calls 3–6 months out, ITM or ATM, when implied volatility is low. Never risk more than 2% of portfolio on an options position.

---

## The Camillo Checklist (Pre-Trade)

Before entering any position, answer these questions honestly:

- [ ] Can I describe the trend in one sentence using plain English (not financial jargon)?
- [ ] Did I observe this trend personally or hear it from a non-investor?
- [ ] Can I buy this product / use this app / visit this place to verify the trend?
- [ ] Does Wall Street know about this? (Check analyst count, recent research)
- [ ] Is the stock price still below where the trend would justify?
- [ ] What is the clear exit scenario?
- [ ] Am I comfortable holding this for 6 months if needed?

If you cannot check all boxes, do not enter.

---

## Historical Examples (Camillo's Framework Applied)

| Company     | Trend Signal                          | Entry Approx | Outcome      |
|:---         |:---                                   |:---          |:---          |
| Crocs        | Kids wearing them, analysts dismissive | 2020         | +400%+       |
| Lululemon    | Yoga/athleisure visible in gyms        | Early 2010s  | Multi-bagger |
| Celsius      | In gyms before it hit convenience stores | 2021        | +500%+       |
| On Running   | Trail runners before mainstream        | 2022 IPO     | Strong run   |
| Birkenstock  | Fashion circles before analyst coverage| 2023 IPO     | Mixed         |

---

## Common Mistakes

1. **Entering after reading about the trend financially** — if you found it in a newsletter or stock screener, you are late.
2. **Confusing a fad with a trend** — a viral TikTok moment is not the same as a multi-year cultural shift.
3. **Ignoring the price** — even the best trend thesis can be a bad trade if the stock already priced it in.
4. **Over-sizing** — Camillo keeps positions small because he knows he will be wrong often. The wins need to cover the losses with room to spare.
5. **Holding too long** — the edge disappears when the trend becomes mainstream. Do not fall in love with a winner.

---

## Quick Reference Card

```
OBSERVE trend in real life
      ↓
SEARCH it on Google Trends (slope rising?)
      ↓
CHECK analyst count (< 10?)
      ↓
SCAN stock price (not run yet?)
      ↓
SCORE with scanner (≥ 70?)
      ↓
ENTER small (≤ 10% portfolio)
      ↓
MONITOR weekly (trend still intact?)
      ↓
EXIT when trend peaks OR Wall Street catches up
```

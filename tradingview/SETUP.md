# TradingView Integration Setup

Two components: a **Pine Script indicator** for chart signals and a **webhook server** that receives TradingView alerts and routes them to the Camillo bot.

---

## 1 — Pine Script Indicator

1. Open TradingView → Pine Editor (bottom panel)
2. Paste the contents of `camillo_indicator.pine`
3. Click **Add to chart**

The indicator adds a lower panel showing the composite 0–100 score with BUY/WATCH/PASS levels, and plots entry/exit arrows on the price chart when score crosses key thresholds.

---

## 2 — Webhook Server

### Start the server

```bash
# Paper trading (safe default)
python3 tradingview/webhook_server.py --mode paper

# With a shared secret (recommended — prevents unauthorized triggers)
TV_WEBHOOK_SECRET=your_secret python3 tradingview/webhook_server.py --mode paper

# Dry run (no real orders, logs only)
python3 tradingview/webhook_server.py --mode dry
```

Health check: `curl http://localhost:8765/health`

### Expose to TradingView (requires public URL)

TradingView's servers POST to your webhook — your laptop must be reachable.
Use [ngrok](https://ngrok.com) to create a tunnel:

```bash
ngrok http 8765
# → Forwarding: https://abc123.ngrok.io → localhost:8765
```

Use `https://abc123.ngrok.io/webhook` as your TradingView alert URL.

---

## 3 — TradingView Alert Setup

In TradingView: **Alerts → Create Alert → Webhook URL**

Set the URL to your public webhook endpoint. Set the **Message** body to one of the JSON payloads below.

### Alert message formats

**Score and auto-buy if signal is strong:**
```json
{"action": "scan", "ticker": "{{ticker}}"}
```

**Force a buy immediately (trust TV's signal):**
```json
{"action": "buy", "ticker": "{{ticker}}", "price": {{close}}}
```

**Exit a position:**
```json
{"action": "exit", "ticker": "{{ticker}}", "price": {{close}}}
```

**Run the full watchlist scan:**
```json
{"action": "scan_all"}
```

### Adding the secret header (if you set TV_WEBHOOK_SECRET)

In the TradingView alert → **Webhook** section → add header:
```
X-TV-Secret: your_secret
```

---

## 4 — Recommended Alert Combinations

| TV Alert condition | Message | Effect |
|---|---|---|
| RSI(14) crosses above 50 | `{"action":"scan","ticker":"{{ticker}}"}` | Score the stock; buy only if score ≥ 70 |
| Price crosses above 20-day SMA | `{"action":"scan","ticker":"{{ticker}}"}` | Same — confirm with Camillo scoring |
| Price drops 15% intraday | `{"action":"exit","ticker":"{{ticker}}","price":{{close}}}` | Hard exit (matches bot's stop-loss rule) |
| Custom Pine alert (score ≥ 70) | `{"action":"buy","ticker":"{{ticker}}","price":{{close}}}` | Direct entry from your chart signal |

"""
TradingView → Camillo Bot webhook server.

TradingView alerts POST JSON to this server.  The server parses the signal
and routes it to the Camillo bot — triggering a focused scan, a buy, or
an exit check for the named ticker.

Setup in TradingView:
  Alert → Webhook URL → http://localhost:8765/webhook
  Alert message (JSON):
    {"action": "scan",  "ticker": "{{ticker}}"}
    {"action": "buy",   "ticker": "{{ticker}}", "price": {{close}}}
    {"action": "exit",  "ticker": "{{ticker}}", "price": {{close}}}
    {"action": "scan_all"}

Run:
  python3 tradingview/webhook_server.py
  python3 tradingview/webhook_server.py --port 8765 --mode paper

Expose to TradingView (requires public URL):
  ngrok http 8765       # then use the https ngrok URL in TradingView alerts
"""

import argparse
import json
import logging
import sys
import os
from datetime import date, datetime

# Allow running from repo root or tradingview/ subdirectory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Load credentials from .env so ALPACA_API_KEY etc. are available
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception:
    pass

from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("camillo_webhook.log"),
    ],
)
log = logging.getLogger("webhook")

# Injected at startup
_bot = None
_secret = None   # optional shared secret for basic auth


def _handle_signal(payload: dict[str, Any]) -> tuple[int, str]:
    """
    Route an incoming TradingView alert payload to the appropriate bot action.

    Supported actions:
      scan       — score a single ticker and buy if signal is strong
      buy        — directly enter a position (skip score gate)
      exit       — trigger exit check for a single ticker
      scan_all   — run the full watchlist scan
    """
    action = payload.get("action", "").lower()
    ticker = payload.get("ticker", "").upper().strip()
    price  = payload.get("price")

    if not action:
        return 400, "missing 'action' field"

    if _bot is None:
        return 503, "bot not initialized"

    # ── scan a single ticker ─────────────────────────────────────────────
    if action == "scan":
        if not ticker:
            return 400, "missing 'ticker' for action=scan"
        log.info("TV alert: SCAN %s (price=%s)", ticker, price)
        try:
            from camillo_social_arbitrage import SocialArbitrageScanner, SAMPLE_WATCHLIST
            # Build a one-ticker watchlist using the default keywords if known
            known = {w["ticker"]: w for w in SAMPLE_WATCHLIST}
            entry = known.get(ticker, {"ticker": ticker, "keywords": [ticker.lower()]})
            scanner = SocialArbitrageScanner()
            sig = scanner.score_ticker(entry["ticker"], entry["keywords"])
            if sig:
                log.info("Score for %s: %.1f (%s)", ticker, sig.composite_score, sig.signal)
                if sig.signal == "BUY":
                    _bot.executor.run_entries([sig])
                    return 200, f"buy order placed for {ticker} (score={sig.composite_score:.1f})"
                return 200, f"{sig.signal} — no order (score={sig.composite_score:.1f})"
            return 200, f"no signal for {ticker}"
        except Exception as exc:
            log.exception("Scan failed for %s", ticker)
            return 500, str(exc)

    # ── force a direct buy (trust TradingView's signal) ─────────────────
    if action == "buy":
        if not ticker:
            return 400, "missing 'ticker' for action=buy"
        log.info("TV alert: BUY %s (price=%s)", ticker, price)
        try:
            account  = _bot.broker.get_account()
            from camillo_bot.risk import size_order
            from camillo_bot.database import DBPosition, TradeLog
            n_pos = len(_bot.db.get_all_positions())
            sizing = size_order(
                ticker         = ticker,
                signal         = "BUY",
                score          = 75.0,   # default conviction for TV-triggered buys
                account_equity = account.equity,
                open_positions = n_pos,
                config         = _bot.config,
            )
            if sizing is None:
                return 200, f"position skipped (max positions or order too small)"
            order = _bot.broker.place_buy(ticker, sizing.notional)
            fill_price = order.filled_avg_price or (price or 0)
            today = date.today().isoformat()
            _bot.db.save_position(DBPosition(
                ticker           = ticker,
                entry_price      = float(fill_price),
                entry_date       = today,
                notional         = sizing.notional,
                keywords         = [ticker.lower()],
                entry_score      = 75.0,
                signal           = "BUY",
                half_taken       = False,
                last_trend_score = 75.0,
                last_trend_check = today,
            ))
            return 200, f"bought {ticker} ${sizing.notional:.2f} @ ${fill_price}"
        except Exception as exc:
            log.exception("Buy failed for %s", ticker)
            return 500, str(exc)

    # ── exit a single position ───────────────────────────────────────────
    if action == "exit":
        if not ticker:
            return 400, "missing 'ticker' for action=exit"
        log.info("TV alert: EXIT %s (price=%s)", ticker, price)
        try:
            pos = _bot.db.get_position(ticker)
            if not pos:
                return 404, f"no open position for {ticker}"
            broker_pos = _bot.broker.get_position(ticker)
            if broker_pos:
                _bot.broker.place_sell(ticker, broker_pos.qty)
            _bot.db.close_position(ticker)
            from camillo_bot.database import TradeLog
            exit_price = float(price or (broker_pos.avg_cost if broker_pos else 0))
            _bot.db.log_trade(TradeLog(
                ticker    = ticker,
                action    = "EXIT",
                reason    = "tradingview_exit",
                price     = exit_price,
                notional  = broker_pos.market_value if broker_pos else 0.0,
                score     = 0.0,
                timestamp = datetime.now().isoformat(),
            ))
            return 200, f"closed position in {ticker}"
        except Exception as exc:
            log.exception("Exit failed for %s", ticker)
            return 500, str(exc)

    # ── full watchlist scan ──────────────────────────────────────────────
    if action == "scan_all":
        log.info("TV alert: SCAN_ALL")
        try:
            _bot.run_scan(force=True)
            return 200, "scan_all triggered"
        except Exception as exc:
            log.exception("scan_all failed")
            return 500, str(exc)

    return 400, f"unknown action: {action!r}"


class WebhookHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        log.debug("HTTP %s", format % args)

    def _send(self, code: int, body: str):
        data = json.dumps({"status": "ok" if code < 400 else "error",
                           "message": body}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, "Camillo webhook server running")
        else:
            self._send(404, "not found")

    def do_POST(self):
        if self.path != "/webhook":
            self._send(404, "not found")
            return

        # Optional shared-secret check (header: X-TV-Secret)
        if _secret:
            provided = self.headers.get("X-TV-Secret", "")
            if provided != _secret:
                log.warning("Rejected request — bad X-TV-Secret from %s", self.client_address)
                self._send(403, "forbidden")
                return

        length = int(self.headers.get("Content-Length", 0))
        if length > 4096:
            self._send(413, "payload too large")
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._send(400, f"invalid JSON: {exc}")
            return

        log.info("Received: %s", payload)
        code, msg = _handle_signal(payload)
        self._send(code, msg)


def main():
    global _bot, _secret

    parser = argparse.ArgumentParser(description="TradingView webhook → Camillo bot")
    parser.add_argument("--port",   type=int, default=int(os.getenv("PORT", 8765)), help="Port to listen on (default 8765)")
    parser.add_argument("--host",   default="0.0.0.0",      help="Bind address (default 0.0.0.0)")
    parser.add_argument("--mode",   choices=["dry", "paper", "live"], default="paper",
                        help="Broker mode (default paper)")
    parser.add_argument("--secret", default=os.getenv("TV_WEBHOOK_SECRET", ""),
                        help="Shared secret for X-TV-Secret header validation")
    args = parser.parse_args()

    _secret = args.secret or None
    if _secret:
        log.info("X-TV-Secret validation enabled")

    from camillo_bot.bot import build_bot
    log.info("Initializing Camillo bot (mode=%s)...", args.mode)
    try:
        _bot = build_bot(mode=args.mode)
    except Exception as exc:
        log.error("Bot init failed: %s", exc)
        sys.exit(1)

    server = HTTPServer((args.host, args.port), WebhookHandler)
    log.info("Webhook server listening on %s:%d", args.host, args.port)
    log.info("TradingView alert URL → http://localhost:%d/webhook", args.port)
    log.info("Health check          → http://localhost:%d/health", args.port)
    if not _secret:
        log.warning("No --secret set — any request can trigger trades. Set TV_WEBHOOK_SECRET env var.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Webhook server stopped")
        server.server_close()


if __name__ == "__main__":
    main()
